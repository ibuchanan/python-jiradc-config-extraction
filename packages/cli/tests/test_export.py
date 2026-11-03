"""CLI behavior using Jira HTTP fixtures, without a live site."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from cli import main as cli

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from _pytest.capture import CaptureFixture
    from _pytest.monkeypatch import MonkeyPatch
    from conftest import FixtureSetup


def _make_fixture_handler(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[httpx.Request], httpx.Response]:
    """Wrap a handler with default responses for common endpoints."""

    def combined_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rest/api/2/serverInfo"):
            return httpx.Response(404)
        if request.url.path.endswith("/rest/api/2/customFields"):
            return httpx.Response(200, json={"values": [], "screens": [], "total": 0})
        if "/options" in request.url.path:
            return httpx.Response(200, json={"options": [], "total": 0})
        if request.url.path.endswith("/rest/api/2/screens"):
            return httpx.Response(200, json={"values": [], "screens": [], "total": 0})
        if "/rest/api/2/screens/" in request.url.path:
            return httpx.Response(200, json=[])
        return handler(request)

    return combined_handler


def test_cli_exports_only_custom_fields(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {"id": "summary", "name": "Summary", "custom": False},
                {
                    "id": "customfield_10001",
                    "name": "Team",
                    "custom": True,
                    "schema": {
                        "custom": (
                            "com.valiantys.jira.plugins.SQLFeed:"
                            "nfeed-standard-customfield-type"
                        ),
                        "type": "string",
                    },
                },
                {"id": "customfield_10002", "name": "Cost", "custom": True},
            ],
        )

    jira_basic_auth_fixture(
        "https://jira.example.com/jira", _make_fixture_handler(handler)
    )

    assert cli.main(["export", "export"]) == 0
    records = [
        json.loads(line)
        for line in (tmp_path / "export" / "fields.jsonl").read_text().splitlines()
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
            "field": {
                "name": "Team",
                "schema": {
                    "custom": (
                        "com.valiantys.jira.plugins.SQLFeed:"
                        "nfeed-standard-customfield-type"
                    )
                },
            },
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
    assert requests[0].headers["Authorization"].startswith("Basic ")
    assert "Extracting fields... .." in capsys.readouterr().out


def test_cli_checkpoints_completed_fields_and_skips_them_on_rerun(
    tmp_path: Path, monkeypatch: MonkeyPatch, jira_basic_auth_fixture: FixtureSetup
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json=[{"id": "customfield_1", "name": "Team", "custom": True}]
        )

    jira_basic_auth_fixture("https://jira.example.com", _make_fixture_handler(handler))

    args = ["export", "export"]
    assert cli.main(args) == 0
    before = (tmp_path / "export" / "fields.jsonl").read_bytes()
    manifest = json.loads((tmp_path / "export" / "manifest.json").read_text())
    checkpoint = json.loads((tmp_path / "export" / "checkpoint.json").read_text())
    assert manifest["schema_version"] == 1
    assert manifest["run_id"]
    assert datetime.fromisoformat(manifest["created_at"]).tzinfo == UTC
    assert manifest["jira_version"] is None
    assert manifest["app_versions"] == {}
    assert manifest["coverage"]["fields"] == {"status": "complete", "count": 1}
    assert checkpoint["stages"]["fields"] == {"status": "complete", "count": 1}

    assert cli.main(args) == 0
    assert len(requests) == 1
    assert (tmp_path / "export" / "fields.jsonl").read_bytes() == before
    assert (
        json.loads((tmp_path / "export" / "manifest.json").read_text())["run_id"]
        == manifest["run_id"]
    )


def test_cli_retries_interrupted_stage_without_duplicate_records(
    tmp_path: Path, monkeypatch: MonkeyPatch, jira_basic_auth_fixture: FixtureSetup
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, json=[{"id": "customfield_1", "name": "Team", "custom": True}]
        )

    jira_basic_auth_fixture("https://jira.example.com", _make_fixture_handler(handler))

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
    args = ["export", "export"]
    assert cli.main(args) == 1
    run_id = json.loads((tmp_path / "export" / "manifest.json").read_text())["run_id"]
    assert len((tmp_path / "export" / "fields.jsonl").read_text().splitlines()) == 1

    assert cli.main(args) == 0
    expected_calls = 2  # initial attempt and retry
    assert calls == expected_calls
    assert (
        json.loads((tmp_path / "export" / "manifest.json").read_text())["run_id"]
        == run_id
    )
    assert json.loads((tmp_path / "export" / "checkpoint.json").read_text())["stages"][
        "fields"
    ] == {"status": "complete", "count": 1}
    assert len((tmp_path / "export" / "fields.jsonl").read_text().splitlines()) == 1


def test_cli_recovers_when_initial_checkpoint_was_not_created(
    tmp_path: Path, monkeypatch: MonkeyPatch, jira_basic_auth_fixture: FixtureSetup
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=[])

    jira_basic_auth_fixture("https://jira.example.com", _make_fixture_handler(handler))

    original_save = cli._save_json
    interrupted = False

    def crash_at_checkpoint(path: Path, data: dict[str, object]) -> None:
        nonlocal interrupted
        if path.name == "checkpoint.json" and not interrupted:
            interrupted = True
            raise OSError("simulated interruption")
        original_save(path, data)

    monkeypatch.setattr(cli, "_save_json", crash_at_checkpoint)
    args = ["export", "export"]
    assert cli.main(args) == 1
    run_id = json.loads((tmp_path / "export" / "manifest.json").read_text())["run_id"]
    assert cli.main(args) == 0
    assert calls == 1
    assert (
        json.loads((tmp_path / "export" / "manifest.json").read_text())["run_id"]
        == run_id
    )


def test_cli_retries_failed_stage_without_persisting_secret(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    calls = 0
    secret = "private-password"  # from fixture

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(403, json={"errorMessages": ["Denied"]})
        return httpx.Response(
            200, json=[{"id": "customfield_1", "name": "Team", "custom": True}]
        )

    jira_basic_auth_fixture("https://jira.example.com", _make_fixture_handler(handler))

    args = ["export", "export"]
    assert cli.main(args) == 1
    failed = json.loads((tmp_path / "export" / "checkpoint.json").read_text())
    assert failed["stages"]["fields"] == {
        "status": "failed",
        "error": "HTTPStatusError",
        "http_status": 403,
    }
    assert secret not in "".join(
        path.read_text() for path in (tmp_path / "export").iterdir() if path.is_file()
    ) + str(capsys.readouterr())
    sibling = tmp_path / "export" / "screens.jsonl"
    sibling.write_text('{"source":{"id":"screen_1"}}\n')
    checkpoint_path = tmp_path / "export" / "checkpoint.json"
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
    tmp_path: Path, monkeypatch: MonkeyPatch, jira_basic_auth_fixture: FixtureSetup
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": "customfield_1", "name": "Old", "custom": True},
                {"id": "customfield_1", "name": "Current", "custom": True},
            ],
        )

    jira_basic_auth_fixture("https://jira.example.com", _make_fixture_handler(handler))

    assert cli.main(["export", "export"]) == 0
    records = [
        json.loads(line)
        for line in (tmp_path / "export" / "fields.jsonl").read_text().splitlines()
    ]
    assert [
        (record["source"]["id"], record["field"]["name"]) for record in records
    ] == [("customfield_1", "Current")]


def test_cli_omits_untrusted_metadata_and_redacts_secret_like_names(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
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

    jira_basic_auth_fixture("https://jira.example.com", _make_fixture_handler(handler))

    assert cli.main(["export", "export"]) == 0
    exported = (tmp_path / "export" / "fields.jsonl").read_text()
    assert secret not in exported + str(capsys.readouterr())
    records = [json.loads(line) for line in exported.splitlines()]
    assert [record["source"]["id"] for record in records] == [
        "customfield_1",
        "customfield_2",
    ]
    assert records[1]["field"] == {"name": "[REDACTED]"}


def test_cli_reports_api_failure_without_config(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(403, json={"errorMessages": ["Denied"]})

    jira_basic_auth_fixture("https://jira.example.com", _make_fixture_handler(handler))

    assert cli.main(["export", "export"]) == 1
    output = capsys.readouterr()
    assert "403" in output.err
    assert "Jira field endpoint" in output.err
    assert "private-password" not in output.out + output.err
    assert not (tmp_path / "export" / "fields.jsonl").exists()


def test_cli_reports_connection_failure_cause(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        error = httpx.ConnectError("connect failed")
        error.__cause__ = OSError("[Errno 8] no host")
        raise error

    jira_basic_auth_fixture("https://jira.example.com", handler)

    assert cli.main(["export", "export"]) == 1

    assert "[Errno 8] no host" in capsys.readouterr().err
    failed = json.loads((tmp_path / "export" / "checkpoint.json").read_text())
    assert failed["stages"]["fields"] == {
        "status": "failed",
        "error": "RequestError",
        "detail": "httpx.ConnectError <- OSError: [Errno 8] no host",
    }
