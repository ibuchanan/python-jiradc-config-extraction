"""Public CLI export of Jira DC custom fields and their options."""

import json
from typing import TYPE_CHECKING

import httpx

from cli import main as cli

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch
    from conftest import FixtureSetup


def _make_fixture_handler(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[httpx.Request], httpx.Response]:
    """Wrap a handler with default responses for common endpoints."""

    def combined_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rest/api/2/serverInfo"):
            return httpx.Response(404)
        if request.url.path.endswith("/rest/api/2/screens"):
            return httpx.Response(200, json={"values": [], "screens": [], "total": 0})
        return handler(request)

    return combined_handler


def test_cli_exports_custom_fields_and_options(
    tmp_path: Path, monkeypatch: MonkeyPatch, jira_basic_auth_fixture: FixtureSetup
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/rest/api/2/field":
            return httpx.Response(
                200, json=[{"id": "customfield_10000", "custom": True, "name": "Team"}]
            )
        if path == "/rest/api/2/customFields":
            return httpx.Response(
                200,
                json={
                    "values": [
                        {
                            "id": "customfield_10000",
                            "projectIds": [42],
                            "issueTypeIds": ["1"],
                            "isAllProjects": False,
                        }
                    ],
                    "total": 1,
                },
            )
        if path == "/rest/api/2/customFields/10000/options":
            return httpx.Response(
                200,
                json={
                    "options": [
                        {"id": 7, "value": "Blue", "disabled": False},
                        {"id": 8, "value": "Green", "disabled": True},
                    ],
                    "total": 2,
                },
            )
        raise AssertionError(f"Unexpected path {path}")

    jira_basic_auth_fixture("https://jira.example.com", _make_fixture_handler(handler))

    assert cli.main(["export", "export"]) == 0
    contexts = [
        json.loads(line)
        for line in (tmp_path / "export" / "custom-fields.jsonl")
        .read_text()
        .splitlines()
    ]
    assert len(contexts) == 1
    assert contexts[0]["source"] == {
        "system": "jira",
        "entity": "custom_fields",
        "id": "customfield_10000",
        "endpoint": "/rest/api/2/customFields",
    }
    assert contexts[0]["field_id"] == "customfield_10000"
    assert contexts[0]["project_ids"] == ["42"]
    assert contexts[0]["issue_type_ids"] == ["1"]
    options = [
        json.loads(line)
        for line in (tmp_path / "export" / "custom-field-options.jsonl")
        .read_text()
        .splitlines()
    ]
    assert [
        (row["source"]["id"], row["field_id"], row["value"], row["disabled"])
        for row in options
    ] == [
        ("customfield_10000:7", "customfield_10000", "Blue", False),
        ("customfield_10000:8", "customfield_10000", "Green", True),
    ]
    manifest = json.loads((tmp_path / "export" / "manifest.json").read_text())
    assert manifest["coverage"]["field_contexts"]["status"] == "unsupported"
    assert manifest["coverage"]["field_configurations"]["status"] == "unsupported"
    assert manifest["coverage"]["field_contexts"]["reason"]
    assert manifest["coverage"]["field_configurations"]["evidence"]
    assert all(request.method == "GET" for request in requests)


def test_cli_paginates_custom_fields_and_options(
    tmp_path: Path, monkeypatch: MonkeyPatch, jira_basic_auth_fixture: FixtureSetup
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/field"):
            return httpx.Response(
                200, json=[{"id": "customfield_1", "custom": True, "name": "Team"}]
            )
        if path.endswith("/customFields"):
            start = int(request.url.params["startAt"])
            return httpx.Response(
                200,
                json={
                    "values": [{"id": "customfield_1", "projectIds": [start + 1]}],
                    "total": 2,
                },
            )
        if path.endswith("/options"):
            page = int(request.url.params["page"])
            return httpx.Response(
                200,
                json={"options": [{"id": page, "value": f"Option {page}"}], "total": 2},
            )
        raise AssertionError(path)

    jira_basic_auth_fixture("https://jira.example.com", _make_fixture_handler(handler))

    assert cli.main(["export", "export"]) == 0
    contexts = [
        json.loads(line)
        for line in (tmp_path / "export" / "custom-fields.jsonl")
        .read_text()
        .splitlines()
    ]
    options = [
        json.loads(line)
        for line in (tmp_path / "export" / "custom-field-options.jsonl")
        .read_text()
        .splitlines()
    ]
    assert [row["project_ids"] for row in contexts] == [["1"]]  # first occurrence wins
    assert [row["source"]["id"] for row in options] == [
        "customfield_1:1",
        "customfield_1:2",
    ]


def test_cli_reports_unauthorized_custom_fields_without_claiming_empty(
    tmp_path: Path, monkeypatch: MonkeyPatch, jira_basic_auth_fixture: FixtureSetup
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/field"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/customFields"):
            return httpx.Response(403, json={"error": "Denied"})
        raise AssertionError(str(request.url))

    jira_basic_auth_fixture("https://jira.example.com", _make_fixture_handler(handler))

    assert cli.main(["export", "export"]) == 1
    assert not (tmp_path / "export" / "custom-fields.jsonl").exists()
    manifest = json.loads((tmp_path / "export" / "manifest.json").read_text())
    checkpoint = json.loads((tmp_path / "export" / "checkpoint.json").read_text())
    assert manifest["coverage"]["custom_fields"]["status"] == "unauthorized"
    forbidden = 403
    assert checkpoint["stages"]["custom_fields"]["http_status"] == forbidden
    assert "private-password" not in "".join(
        path.read_text() for path in (tmp_path / "export").iterdir()
    )
    assert manifest["coverage"]["custom_field_options"] == {
        "status": "complete",
        "count": 0,
    }


def test_cli_reports_unsupported_options_and_empty_custom_fields(
    tmp_path: Path, monkeypatch: MonkeyPatch, jira_basic_auth_fixture: FixtureSetup
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/field"):
            return httpx.Response(200, json=[{"id": "customfield_1", "custom": True}])
        if request.url.path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "screens": [], "total": 0})
        if request.url.path.endswith("/options"):
            return httpx.Response(404, json={"error": "Not found"})
        raise AssertionError(str(request.url))

    jira_basic_auth_fixture("https://jira.example.com", _make_fixture_handler(handler))

    assert cli.main(["export", "export"]) == 1
    assert (tmp_path / "export" / "custom-fields.jsonl").read_text() == ""
    assert not (tmp_path / "export" / "custom-field-options.jsonl").exists()
    coverage = json.loads((tmp_path / "export" / "manifest.json").read_text())[
        "coverage"
    ]
    assert coverage["custom_fields"] == {"status": "complete", "count": 0}
    assert coverage["custom_field_options"]["status"] == "unsupported"
    assert (
        "private-password" not in (tmp_path / "export" / "checkpoint.json").read_text()
    )


def test_cli_retries_incomplete_options_without_duplication(
    tmp_path: Path, monkeypatch: MonkeyPatch, jira_basic_auth_fixture: FixtureSetup
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith("/field"):
            return httpx.Response(200, json=[{"id": "customfield_1", "custom": True}])
        if request.url.path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "screens": [], "total": 0})
        if request.url.path.endswith("/options"):
            attempts += 1
            if attempts == 1:
                return httpx.Response(503)
            return httpx.Response(
                200, json={"options": [{"id": 1, "value": "One"}], "total": 1}
            )
        raise AssertionError(str(request.url))

    jira_basic_auth_fixture("https://jira.example.com", _make_fixture_handler(handler))

    args = ["export", "export"]
    assert cli.main(args) == 1
    assert (
        json.loads((tmp_path / "export" / "checkpoint.json").read_text())["stages"][
            "custom_field_options"
        ]["status"]
        == "failed"
    )
    assert cli.main(args) == 0
    expected_attempts = 2
    assert attempts == expected_attempts
    assert (
        len(
            (tmp_path / "export" / "custom-field-options.jsonl")
            .read_text()
            .splitlines()
        )
        == 1
    )
    assert json.loads((tmp_path / "export" / "checkpoint.json").read_text())["stages"][
        "custom_field_options"
    ] == {"status": "complete", "count": 1}
