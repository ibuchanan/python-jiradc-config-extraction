"""Dynamic Forms coverage at the CLI output seam."""

import json
from typing import TYPE_CHECKING

import httpx

from cli import main as cli

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch
    from conftest import FixtureSetup


def test_cli_reports_dynamic_forms_not_queried_without_supported_api(
    tmp_path: Path, monkeypatch: MonkeyPatch, jira_basic_auth_fixture: FixtureSetup
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/serverInfo"):
            return httpx.Response(404)
        if request.url.path.endswith("/field"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/customFields") or request.url.path.endswith(
            "/screens"
        ):
            return httpx.Response(200, json={"values": [], "total": 0})
        raise AssertionError(f"Unexpected API request: {request.url.path}")

    jira_basic_auth_fixture("https://jira.example.com", respond)

    args = ["export"]
    assert cli.main(args) == 0
    coverage = json.loads((tmp_path / "export" / "manifest.json").read_text())[
        "coverage"
    ]
    assert coverage["dynamic_forms"] == {
        "status": "unsupported",
        "reason": "no supported read-only Dynamic Forms DC REST API identified",
    }
    assert not (tmp_path / "export" / "dynamic-forms.jsonl").exists()
    assert not (tmp_path / "export" / "associations.jsonl").exists()
    assert all(request.method == "GET" for request in requests)

    assert cli.main(args) == 0
    assert (
        json.loads((tmp_path / "export" / "manifest.json").read_text())["coverage"][
            "dynamic_forms"
        ]
        == coverage["dynamic_forms"]
    )
