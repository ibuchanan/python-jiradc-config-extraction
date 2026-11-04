"""A failed stage keeps its committed records and resumes without refetching."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import httpx

from cli import main as cli

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.capture import CaptureFixture
    from conftest import FixtureSetup


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_tab_fields_resume_from_the_failed_tab(
    tmp_path: Path,
    jira_basic_auth_fixture: FixtureSetup,
    capsys: CaptureFixture[str],
) -> None:
    tab_field_requests: list[str] = []
    fail_tab_six = True

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        nonlocal fail_tab_six
        path = request.url.path
        if path.endswith("/serverInfo"):
            return httpx.Response(404)
        if path.endswith("/field"):
            return httpx.Response(200, json=[])
        if path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "total": 0})
        if path.endswith("/screens"):
            return httpx.Response(
                200, json={"screens": [{"id": 10, "name": "S"}], "total": 1}
            )
        if path.endswith("/screens/10/tabs"):
            return httpx.Response(200, json=[{"id": 5, "name": "A"}, {"id": 6}])
        if path.endswith("/fields"):
            tab = path.split("/")[-2]
            tab_field_requests.append(tab)
            if tab == "6" and fail_tab_six:
                return httpx.Response(503, json={"error": "down"})
            return httpx.Response(200, json=[{"id": f"field_{tab}"}])
        raise AssertionError(path)

    jira_basic_auth_fixture("https://jira.example.com", handler)
    export = tmp_path / "export"

    assert cli.main(["export", "export"]) == 1
    assert re.search(
        r"(?m)^screen_tab_fields\s+failed \(1 records written\)\s*$",
        capsys.readouterr().out,
    )

    tab_fields = export / "screen-tab-fields.jsonl"
    assert [row["source"]["id"] for row in _lines(tab_fields)] == ["10:5:field_5"]
    checkpoint = json.loads((export / "checkpoint.json").read_text())
    assert checkpoint["progress"]["screen_tab_fields"] == {
        "cursor": 1,
        "offset": tab_fields.stat().st_size,
        "count": 1,
    }
    assert checkpoint["stages"]["screen_tab_fields"] == {
        "status": "failed",
        "error": "HTTPStatusError",
        "http_status": 503,
        "records_written": 1,
    }

    fail_tab_six = False
    assert cli.main(["export", "export"]) == 0

    assert tab_field_requests == ["5", "6", "6"]  # tab 5 was not fetched again
    assert [row["source"]["id"] for row in _lines(tab_fields)] == [
        "10:5:field_5",
        "10:6:field_6",
    ]
    checkpoint = json.loads((export / "checkpoint.json").read_text())
    assert "screen_tab_fields" not in checkpoint["progress"]
    assert checkpoint["stages"]["screen_tab_fields"] == {
        "status": "complete",
        "count": 2,
    }


def test_paged_stage_resumes_at_the_saved_offset(
    tmp_path: Path, jira_basic_auth_fixture: FixtureSetup
) -> None:
    screen_offsets: list[int] = []
    fail_second_page = True

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fail_second_page
        path = request.url.path
        if path.endswith("/serverInfo"):
            return httpx.Response(404)
        if path.endswith("/field"):
            return httpx.Response(200, json=[])
        if path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "total": 0})
        if path.endswith("/screens"):
            start = int(request.url.params["startAt"])
            screen_offsets.append(start)
            if start == 1 and fail_second_page:
                return httpx.Response(503, json={"error": "down"})
            return httpx.Response(
                200,
                json={"screens": [{"id": 10 + start, "name": "S"}], "total": 3},
            )
        if path.endswith("/tabs"):
            return httpx.Response(200, json=[])
        raise AssertionError(path)

    jira_basic_auth_fixture("https://jira.example.com", handler)

    assert cli.main(["export", "export"]) == 1
    fail_second_page = False
    assert cli.main(["export", "export"]) == 0

    assert screen_offsets == [0, 1, 1, 2]  # page 0 was not fetched again
    screens = tmp_path / "export" / "screens.jsonl"
    assert [row["source"]["id"] for row in _lines(screens)] == ["10", "11", "12"]
