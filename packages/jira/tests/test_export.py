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
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(200, json={"values": [], "total": 0})
                    if request.url.path.endswith("/rest/api/2/customFields")
                    else httpx.Response(200, json={"options": [], "total": 0})
                    if "/options" in request.url.path
                    else handler(request)
                )
            ),
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


def test_cli_checkpoints_completed_fields_and_skips_them_on_rerun(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json=[{"id": "customfield_1", "name": "Team", "custom": True}]
        )

    use_http_fixture(monkeypatch, handler)
    monkeypatch.setenv("JIRA_PAT", "my-secret-pat")
    args = ["https://jira.example.com", str(tmp_path)]
    assert cli.main(args) == 0
    before = (tmp_path / "fields.jsonl").read_bytes()
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    assert manifest["schema_version"] == 1
    assert manifest["run_id"]
    assert datetime.fromisoformat(manifest["created_at"]).tzinfo == UTC
    assert manifest["jira_version"] is None
    assert manifest["app_versions"] == {}
    assert manifest["coverage"]["fields"] == {"status": "complete", "count": 1}
    assert checkpoint["stages"]["fields"] == {"status": "complete", "count": 1}

    assert cli.main(args) == 0
    assert len(requests) == 1
    assert (tmp_path / "fields.jsonl").read_bytes() == before
    assert (
        json.loads((tmp_path / "manifest.json").read_text())["run_id"]
        == manifest["run_id"]
    )


def test_cli_retries_interrupted_stage_without_duplicate_records(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, json=[{"id": "customfield_1", "name": "Team", "custom": True}]
        )

    use_http_fixture(monkeypatch, handler)
    monkeypatch.setenv("JIRA_PAT", "my-secret-pat")
    original_save = cli._save_json
    interrupted = False

    def crash_before_checkpoint(path: Path, data: dict[str, object]) -> None:
        nonlocal interrupted
        stages = data.get("stages")
        if (
            path.name == "checkpoint.json"
            and isinstance(stages, dict)
            and stages.get("fields", {}).get("status") == "complete"
            and not interrupted
        ):
            interrupted = True
            raise OSError("simulated interruption")
        original_save(path, data)

    monkeypatch.setattr(cli, "_save_json", crash_before_checkpoint)
    args = ["https://jira.example.com", str(tmp_path)]
    assert cli.main(args) == 1
    run_id = json.loads((tmp_path / "manifest.json").read_text())["run_id"]
    assert len((tmp_path / "fields.jsonl").read_text().splitlines()) == 1

    assert cli.main(args) == 0
    expected_calls = 2  # initial attempt and retry
    assert calls == expected_calls
    assert json.loads((tmp_path / "manifest.json").read_text())["run_id"] == run_id
    assert json.loads((tmp_path / "checkpoint.json").read_text())["stages"][
        "fields"
    ] == {"status": "complete", "count": 1}
    assert len((tmp_path / "fields.jsonl").read_text().splitlines()) == 1


def test_cli_recovers_when_initial_checkpoint_was_not_created(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=[])

    use_http_fixture(monkeypatch, handler)
    monkeypatch.setenv("JIRA_PAT", "my-secret-pat")
    original_save = cli._save_json
    interrupted = False

    def crash_at_checkpoint(path: Path, data: dict[str, object]) -> None:
        nonlocal interrupted
        if path.name == "checkpoint.json" and not interrupted:
            interrupted = True
            raise OSError("simulated interruption")
        original_save(path, data)

    monkeypatch.setattr(cli, "_save_json", crash_at_checkpoint)
    args = ["https://jira.example.com", str(tmp_path)]
    assert cli.main(args) == 1
    run_id = json.loads((tmp_path / "manifest.json").read_text())["run_id"]
    assert cli.main(args) == 0
    assert calls == 1
    assert json.loads((tmp_path / "manifest.json").read_text())["run_id"] == run_id


def test_cli_retries_failed_stage_without_persisting_secret(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    calls = 0
    secret = "my-secret-pat"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(403, json={"errorMessages": [secret]})
        return httpx.Response(
            200, json=[{"id": "customfield_1", "name": "Team", "custom": True}]
        )

    use_http_fixture(monkeypatch, handler)
    monkeypatch.setenv("JIRA_PAT", secret)
    args = ["https://jira.example.com", str(tmp_path)]
    assert cli.main(args) == 1
    failed = json.loads((tmp_path / "checkpoint.json").read_text())
    assert failed["stages"]["fields"] == {
        "status": "failed",
        "error": "HTTPStatusError",
        "http_status": 403,
    }
    assert secret not in "".join(
        path.read_text() for path in tmp_path.iterdir() if path.is_file()
    ) + str(capsys.readouterr())
    sibling = tmp_path / "screens.jsonl"
    sibling.write_text('{"source":{"id":"screen_1"}}\n')
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["stages"]["screens"] = {"status": "complete", "count": 1}
    checkpoint_path.write_text(json.dumps(checkpoint))

    assert cli.main(args) == 0
    expected_calls = 2  # failed attempt and retry
    assert calls == expected_calls
    stages = json.loads(checkpoint_path.read_text())["stages"]
    assert stages["fields"] == {"status": "complete", "count": 1}
    assert stages["screens"] == {"status": "complete", "count": 1}
    assert sibling.read_text() == '{"source":{"id":"screen_1"}}\n'


def test_cli_upserts_repeated_source_ids(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": "customfield_1", "name": "Old", "custom": True},
                {"id": "customfield_1", "name": "Current", "custom": True},
            ],
        )

    use_http_fixture(monkeypatch, handler)
    monkeypatch.setenv("JIRA_PAT", "my-secret-pat")
    assert cli.main(["https://jira.example.com", str(tmp_path)]) == 0
    records = [
        json.loads(line)
        for line in (tmp_path / "fields.jsonl").read_text().splitlines()
    ]
    assert [
        (record["source"]["id"], record["field"]["name"]) for record in records
    ] == [("customfield_1", "Current")]


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
