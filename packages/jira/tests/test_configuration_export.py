"""Public CLI export of Jira DC field applicability and options."""

import json
from typing import TYPE_CHECKING

import httpx

from jira import cli

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch


def use_fixture(
    monkeypatch: MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
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
    monkeypatch.setenv("JIRA_PAT", "test-pat")


def test_cli_exports_field_applicability_and_options(
    tmp_path: Path, monkeypatch: MonkeyPatch
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

    use_fixture(monkeypatch, handler)
    assert cli.main(["https://jira.example.com", str(tmp_path)]) == 0
    contexts = [
        json.loads(line)
        for line in (tmp_path / "field-contexts.jsonl").read_text().splitlines()
    ]
    assert len(contexts) == 1
    assert contexts[0]["source"] == {
        "system": "jira",
        "entity": "field_applicability",
        "id": "customfield_10000",
        "endpoint": "/rest/api/2/customFields",
    }
    assert contexts[0]["field_id"] == "customfield_10000"
    assert contexts[0]["project_ids"] == ["42"]
    assert contexts[0]["issue_type_ids"] == ["1"]
    options = [
        json.loads(line)
        for line in (tmp_path / "field-options.jsonl").read_text().splitlines()
    ]
    assert [
        (row["source"]["id"], row["field_id"], row["value"], row["disabled"])
        for row in options
    ] == [
        ("customfield_10000:7", "customfield_10000", "Blue", False),
        ("customfield_10000:8", "customfield_10000", "Green", True),
    ]
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["coverage"]["field_configurations"]["status"] == "unsupported"
    assert manifest["coverage"]["explicit_field_contexts"]["status"] == "unsupported"
    assert all(request.method == "GET" for request in requests)


def test_cli_paginates_applicability_and_options(
    tmp_path: Path, monkeypatch: MonkeyPatch
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

    use_fixture(monkeypatch, handler)
    assert cli.main(["https://jira.example.com", str(tmp_path)]) == 0
    contexts = [
        json.loads(line)
        for line in (tmp_path / "field-contexts.jsonl").read_text().splitlines()
    ]
    options = [
        json.loads(line)
        for line in (tmp_path / "field-options.jsonl").read_text().splitlines()
    ]
    assert [row["project_ids"] for row in contexts] == [["2"]]
    assert [row["source"]["id"] for row in options] == [
        "customfield_1:1",
        "customfield_1:2",
    ]


def test_cli_reports_unauthorized_applicability_without_claiming_empty(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/field"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/customFields"):
            return httpx.Response(403, json={"error": "test-pat"})
        raise AssertionError(str(request.url))

    use_fixture(monkeypatch, handler)
    assert cli.main(["https://jira.example.com", str(tmp_path)]) == 0
    assert not (tmp_path / "field-contexts.jsonl").exists()
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    assert manifest["coverage"]["field_applicability"]["status"] == "unauthorized"
    forbidden = 403
    assert checkpoint["stages"]["field_applicability"]["http_status"] == forbidden
    assert "test-pat" not in "".join(path.read_text() for path in tmp_path.iterdir())
    assert manifest["coverage"]["field_options"] == {"status": "complete", "count": 0}


def test_cli_reports_unsupported_options_and_empty_applicability(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/field"):
            return httpx.Response(200, json=[{"id": "customfield_1", "custom": True}])
        if request.url.path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "total": 0})
        if request.url.path.endswith("/options"):
            return httpx.Response(404, json={"error": "test-pat"})
        raise AssertionError(str(request.url))

    use_fixture(monkeypatch, handler)
    assert cli.main(["https://jira.example.com", str(tmp_path)]) == 0
    assert (tmp_path / "field-contexts.jsonl").read_text() == ""
    assert not (tmp_path / "field-options.jsonl").exists()
    coverage = json.loads((tmp_path / "manifest.json").read_text())["coverage"]
    assert coverage["field_applicability"] == {"status": "complete", "count": 0}
    assert coverage["field_options"]["status"] == "unsupported"
    assert "test-pat" not in (tmp_path / "checkpoint.json").read_text()


def test_cli_retries_incomplete_options_without_duplication(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith("/field"):
            return httpx.Response(200, json=[{"id": "customfield_1", "custom": True}])
        if request.url.path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "total": 0})
        if request.url.path.endswith("/options"):
            attempts += 1
            if attempts == 1:
                return httpx.Response(503)
            return httpx.Response(
                200, json={"options": [{"id": 1, "value": "One"}], "total": 1}
            )
        raise AssertionError(str(request.url))

    use_fixture(monkeypatch, handler)
    args = ["https://jira.example.com", str(tmp_path)]
    assert cli.main(args) == 0
    assert (
        json.loads((tmp_path / "checkpoint.json").read_text())["stages"][
            "field_options"
        ]["status"]
        == "failed"
    )
    assert cli.main(args) == 0
    expected_attempts = 2
    assert attempts == expected_attempts
    assert len((tmp_path / "field-options.jsonl").read_text().splitlines()) == 1
    assert json.loads((tmp_path / "checkpoint.json").read_text())["stages"][
        "field_options"
    ] == {"status": "complete", "count": 1}
