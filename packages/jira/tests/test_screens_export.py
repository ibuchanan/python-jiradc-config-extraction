"""Screen inventory at the public CLI and JSONL seam."""

import json
from typing import TYPE_CHECKING

import httpx

from jira import cli

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch


def fixture(
    monkeypatch: MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    real_client = cli.JiraClient
    monkeypatch.setenv("JIRA_PAT", "test-pat")
    monkeypatch.setattr(
        cli,
        "JiraClient",
        lambda url, token: real_client(
            url,
            token,
            requests_per_minute=60_000,
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(404)
                    if request.url.path.endswith("/rest/api/2/serverInfo")
                    else handler(request)
                )
            ),
        ),
    )


def records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_cli_exports_screens_tabs_and_placements(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/field"):
            return httpx.Response(200, json=[])
        if path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "total": 0})
        if path.endswith("/screens"):
            start = int(request.url.params["startAt"])
            return httpx.Response(
                200,
                json={
                    "values": [
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

    fixture(monkeypatch, handler)
    assert cli.main(["https://jira.example.com", str(tmp_path)]) == 0
    screens = records(tmp_path / "screens.jsonl")
    tabs = records(tmp_path / "screen-tabs.jsonl")
    placements = records(tmp_path / "screen-placements.jsonl")
    assert [row["source"]["id"] for row in screens] == ["10", "11"]
    assert screens[0]["screen"] == {"name": "Screen 0"}
    assert [(row["source"]["id"], row["screen_id"]) for row in tabs] == [
        ("10:5", "10"),
        ("11:5", "11"),
    ]
    assert [
        (row["screen_id"], row["tab_id"], row["field_id"], row["position"])
        for row in placements
    ] == [
        ("10", "5", "summary", 0),
        ("10", "5", "customfield_7", 1),
        ("11", "5", "summary", 0),
        ("11", "5", "customfield_7", 1),
    ]
    assert all(
        row["source"]["endpoint"].startswith("/rest/api/2/screens")
        for row in screens + tabs + placements
    )
    assert all(
        row["schema_version"] == 1 and row["extracted_at"]
        for row in screens + tabs + placements
    )
    coverage = json.loads((tmp_path / "manifest.json").read_text())["coverage"]
    assert coverage["screens"] == {"status": "complete", "count": 2}
    assert coverage["screen_tabs"]["count"] == len(tabs)
    assert coverage["screen_placements"]["count"] == len(placements)
    assert coverage["screen_schemes"]["status"] == "unsupported"
    assert coverage["issue_type_screen_schemes"]["status"] == "unsupported"
    assert "hidden" not in "".join(
        p.read_text() for p in tmp_path.iterdir() if p.is_file()
    )
    assert all(request.method == "GET" for request in requests)


def test_cli_distinguishes_empty_from_unauthorized_screen_tabs(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/field"):
            return httpx.Response(200, json=[])
        if path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "total": 0})
        if path.endswith("/screens"):
            return httpx.Response(
                200, json={"values": [{"id": 10, "name": "Main"}], "total": 1}
            )
        if path.endswith("/tabs"):
            return httpx.Response(403, json={"error": "test-pat"})
        raise AssertionError(path)

    fixture(monkeypatch, handler)
    assert cli.main(["https://jira.example.com", str(tmp_path)]) == 1
    coverage = json.loads((tmp_path / "manifest.json").read_text())["coverage"]
    assert coverage["screens"] == {"status": "complete", "count": 1}
    assert coverage["screen_tabs"]["status"] == "unauthorized"
    assert coverage["screen_placements"]["status"] == "not_queried"
    assert not (tmp_path / "screen-tabs.jsonl").exists()
    assert "test-pat" not in "".join(
        p.read_text() for p in tmp_path.iterdir() if p.is_file()
    )


def test_cli_empty_screens_are_complete(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/field"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/customFields") or request.url.path.endswith(
            "/screens"
        ):
            return httpx.Response(200, json={"values": [], "total": 0})
        raise AssertionError(str(request.url))

    fixture(monkeypatch, handler)
    assert cli.main(["https://jira.example.com", str(tmp_path)]) == 0
    coverage = json.loads((tmp_path / "manifest.json").read_text())["coverage"]
    for stage, filename in (
        ("screens", "screens.jsonl"),
        ("screen_tabs", "screen-tabs.jsonl"),
        ("screen_placements", "screen-placements.jsonl"),
    ):
        assert coverage[stage] == {"status": "complete", "count": 0}
        assert (tmp_path / filename).read_text() == ""


def test_cli_retries_failed_placements_without_duplication(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        path = request.url.path
        if path.endswith("/field"):
            return httpx.Response(200, json=[])
        if path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "total": 0})
        if path.endswith("/screens"):
            return httpx.Response(
                200, json={"values": [{"id": 10, "name": "Screen"}], "total": 1}
            )
        if path.endswith("/tabs"):
            return httpx.Response(200, json=[{"id": 5, "name": "Tab"}])
        if path.endswith("/fields"):
            attempts += 1
            if attempts == 1:
                return httpx.Response(503, json={"error": "test-pat"})
            return httpx.Response(200, json=[{"id": "summary"}, {"id": "summary"}])
        raise AssertionError(path)

    fixture(monkeypatch, handler)
    args = ["https://jira.example.com", str(tmp_path)]
    assert cli.main(args) == 1
    coverage = json.loads((tmp_path / "manifest.json").read_text())["coverage"]
    assert coverage["screen_placements"]["status"] == "failed"
    assert not (tmp_path / "screen-placements.jsonl").exists()
    assert cli.main(args) == 0
    expected_attempts = 2  # first attempt and full-stage retry
    assert attempts == expected_attempts
    assert [
        row["source"]["id"] for row in records(tmp_path / "screen-placements.jsonl")
    ] == ["10:5:summary"]
    assert json.loads((tmp_path / "checkpoint.json").read_text())["stages"][
        "screen_placements"
    ] == {"status": "complete", "count": 1}
    assert "test-pat" not in "".join(
        p.read_text() for p in tmp_path.iterdir() if p.is_file()
    )
