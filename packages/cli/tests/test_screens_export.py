"""Screen inventory at the public CLI and JSONL seam."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx

from cli import main as cli

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch
    from conftest import FixtureSetup


def records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_cli_exports_screens_tabs_and_tab_fields(
    tmp_path: Path, monkeypatch: MonkeyPatch, jira_basic_auth_fixture: FixtureSetup
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/serverInfo"):
            return httpx.Response(404)
        if path.endswith("/field"):
            return httpx.Response(200, json=[])
        if path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "screens": [], "total": 0})
        if path.endswith("/screens"):
            start = int(request.url.params["startAt"])
            return httpx.Response(
                200,
                json={
                    "screens": [
                        {
                            "id": 10 + start,
                            "name": f"Screen {start}",
                            "description": "password=hidden",
                        }
                    ],
                    "total": 2,
                },
            )
        if path.endswith("/tabs"):
            return httpx.Response(200, json=[{"id": 5, "name": "Main"}])
        if path.endswith("/fields"):
            return httpx.Response(
                200,
                json=[
                    {"id": "summary", "name": "Summary"},
                    {"id": "customfield_7", "name": "Team", "showWhenEmpty": True},
                ],
            )
        raise AssertionError(path)

    jira_basic_auth_fixture("https://jira.example.com", handler)
    assert cli.main(["export", "export"]) == 0
    screens = records(tmp_path / "export" / "screens.jsonl")
    tabs = records(tmp_path / "export" / "screen-tabs.jsonl")
    tab_fields = records(tmp_path / "export" / "screen-tab-fields.jsonl")
    assert [row["source"]["id"] for row in screens] == ["10", "11"]
    assert screens[0]["screen"] == {"name": "Screen 0"}
    assert [(row["source"]["id"], row["screen_id"]) for row in tabs] == [
        ("10:5", "10"),
        ("11:5", "11"),
    ]
    assert [
        (row["screen_id"], row["tab_id"], row["field_id"], row["position"])
        for row in tab_fields
    ] == [
        ("10", "5", "summary", 0),
        ("10", "5", "customfield_7", 1),
        ("11", "5", "summary", 0),
        ("11", "5", "customfield_7", 1),
    ]
    assert all(
        row["source"]["endpoint"].startswith("/rest/api/2/screens")
        for row in screens + tabs + tab_fields
    )
    assert all(
        row["schema_version"] == 1 and row["extracted_at"]
        for row in screens + tabs + tab_fields
    )
    coverage = json.loads((tmp_path / "export" / "manifest.json").read_text())[
        "coverage"
    ]
    assert coverage["screens"] == {"status": "complete", "count": 2}
    assert coverage["screen_tabs"]["count"] == len(tabs)
    assert coverage["screen_tab_fields"]["count"] == len(tab_fields)
    assert coverage["screen_schemes"]["status"] == "unsupported"
    assert coverage["issue_type_screen_schemes"]["status"] == "unsupported"
    assert coverage["screen_schemes"]["reason"]
    assert coverage["issue_type_screen_schemes"]["evidence"]
    assert "hidden" not in "".join(
        p.read_text() for p in tmp_path.iterdir() if p.is_file()
    )
    assert all(request.method == "GET" for request in requests)


def test_cli_distinguishes_empty_from_unauthorized_screen_tabs(
    tmp_path: Path, monkeypatch: MonkeyPatch, jira_basic_auth_fixture: FixtureSetup
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/serverInfo"):
            return httpx.Response(404)
        if path.endswith("/field"):
            return httpx.Response(200, json=[])
        if path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "screens": [], "total": 0})
        if path.endswith("/screens"):
            return httpx.Response(
                200, json={"screens": [{"id": 10, "name": "Main"}], "total": 1}
            )
        if path.endswith("/tabs"):
            return httpx.Response(403, json={"error": "Denied"})
        raise AssertionError(path)

    jira_basic_auth_fixture("https://jira.example.com", handler)
    assert cli.main(["export", "export"]) == 1
    coverage = json.loads((tmp_path / "export" / "manifest.json").read_text())[
        "coverage"
    ]
    assert coverage["screens"] == {"status": "complete", "count": 1}
    assert coverage["screen_tabs"]["status"] == "unauthorized"
    assert coverage["screen_tab_fields"]["status"] == "not_queried"
    assert not (tmp_path / "export" / "screen-tabs.jsonl").exists()
    assert "private-password" not in "".join(
        p.read_text() for p in (tmp_path / "export").iterdir() if p.is_file()
    )


def test_cli_empty_screens_are_complete(
    tmp_path: Path, monkeypatch: MonkeyPatch, jira_basic_auth_fixture: FixtureSetup
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/serverInfo"):
            return httpx.Response(404)
        if request.url.path.endswith("/field"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/customFields") or request.url.path.endswith(
            "/screens"
        ):
            return httpx.Response(200, json={"values": [], "screens": [], "total": 0})
        raise AssertionError(str(request.url))

    jira_basic_auth_fixture("https://jira.example.com", handler)
    assert cli.main(["export", "export"]) == 0
    coverage = json.loads((tmp_path / "export" / "manifest.json").read_text())[
        "coverage"
    ]
    for stage, filename in (
        ("screens", "screens.jsonl"),
        ("screen_tabs", "screen-tabs.jsonl"),
        ("screen_tab_fields", "screen-tab-fields.jsonl"),
    ):
        assert coverage[stage] == {"status": "complete", "count": 0}
        assert (tmp_path / "export" / filename).read_text() == ""


def test_cli_retries_failed_tab_fields_without_duplication(
    tmp_path: Path, monkeypatch: MonkeyPatch, jira_basic_auth_fixture: FixtureSetup
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        path = request.url.path
        if path.endswith("/serverInfo") or path.endswith("/field"):
            return httpx.Response(404 if path.endswith("/serverInfo") else 200, json=[])
        if path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "screens": [], "total": 0})
        if path.endswith("/screens"):
            return httpx.Response(
                200, json={"screens": [{"id": 10, "name": "Screen"}], "total": 1}
            )
        if path.endswith("/tabs"):
            return httpx.Response(200, json=[{"id": 5, "name": "Tab"}])
        if path.endswith("/fields"):
            attempts += 1
            return httpx.Response(
                503 if attempts == 1 else 200,
                json={"error": "Error"}
                if attempts == 1
                else [{"id": "summary"}, {"id": "summary"}],
            )
        raise AssertionError(path)

    jira_basic_auth_fixture("https://jira.example.com", handler)
    args = ["export", "export"]
    assert cli.main(args) == 1
    coverage = json.loads((tmp_path / "export" / "manifest.json").read_text())[
        "coverage"
    ]
    assert coverage["screen_tab_fields"]["status"] == "failed"
    assert not (tmp_path / "export" / "screen-tab-fields.jsonl").exists()
    assert cli.main(args) == 0
    expected_attempts = 2  # first attempt and full-stage retry
    assert attempts == expected_attempts
    assert [
        row["source"]["id"]
        for row in records(tmp_path / "export" / "screen-tab-fields.jsonl")
    ] == ["10:5:summary"]
    assert json.loads((tmp_path / "export" / "checkpoint.json").read_text())["stages"][
        "screen_tab_fields"
    ] == {"status": "complete", "count": 1}
    assert "private-password" not in "".join(
        p.read_text() for p in (tmp_path / "export").iterdir() if p.is_file()
    )
