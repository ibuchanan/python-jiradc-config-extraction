"""The CLI exports the project -> issue type -> scheme inventories."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx

from cli import main as cli

if TYPE_CHECKING:
    from pathlib import Path

    from conftest import FixtureSetup


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _handler(*, projects_status: int = 200) -> Any:  # noqa: ANN401
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: PLR0911, PLR0912
        path = request.url.path
        if path.endswith("/serverInfo"):
            return httpx.Response(404)
        if path.endswith("/rest/api/2/field"):
            return httpx.Response(200, json=[])
        if path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "total": 0})
        if path.endswith("/rest/plugins/1.0/"):
            return httpx.Response(200, json={"plugins": [], "total": 0})
        if path.endswith("/rest/api/2/screens"):
            return httpx.Response(200, json={"screens": [], "total": 0})
        if path.endswith("/rest/api/2/project"):
            if projects_status != httpx.codes.OK:
                return httpx.Response(projects_status, json={"error": "denied"})
            return httpx.Response(
                200, json=[{"id": "10000", "key": "ABC", "name": "Alpha"}]
            )
        if path.endswith("/rest/api/2/issuetype"):
            return httpx.Response(200, json=[{"id": "1", "name": "Bug"}])
        if path.endswith("/rest/api/2/issuetypescheme"):
            return httpx.Response(200, json={"schemes": [{"id": "10300"}]})
        if path.endswith("/associations"):
            return httpx.Response(200, json=[{"id": "10000", "key": "ABC"}])
        if path.endswith("/rest/api/2/issuetypescheme/10300"):
            return httpx.Response(
                200, json={"id": "10300", "name": "S", "issueTypes": [{"id": "1"}]}
            )
        if path.endswith("/rest/projectconfig/1/issuetype/ABC/1/fields"):
            return httpx.Response(
                200,
                json={
                    "viewScreen": {
                        "screenId": 7,
                        "screenName": "Default",
                        "state": "ACTIVE",
                        "sharedWithProjects": [{"id": 10000, "key": "ABC"}],
                        "sharedWithIssueTypes": ["1"],
                        "totalProjectsCount": 1,
                        "hiddenProjectsCount": 0,
                    }
                },
            )
        if path.endswith("/project/ABC/workflowscheme"):
            return httpx.Response(
                200, json={"id": 1, "name": "W", "defaultWorkflow": "jira"}
            )
        raise AssertionError(path)

    return handler


def test_cli_exports_projects_issue_types_and_schemes(
    tmp_path: Path, jira_basic_auth_fixture: FixtureSetup
) -> None:
    jira_basic_auth_fixture("https://jira.example.com", _handler(), defaults=False)

    assert cli.main(["export", "export"]) == 0

    export = tmp_path / "export"
    assert [r["project"]["key"] for r in _lines(export / "projects.jsonl")] == ["ABC"]
    assert [r["issue_type"]["name"] for r in _lines(export / "issue-types.jsonl")] == [
        "Bug"
    ]
    scheme = _lines(export / "issue-type-schemes.jsonl")[0]["issue_type_scheme"]
    assert scheme["issue_type_ids"] == ["1"]
    assert scheme["project_ids"] == ["10000"]
    workflow = _lines(export / "project-workflow-schemes.jsonl")[0]
    assert workflow["project_id"] == "10000"
    assert workflow["default_workflow"] == "jira"
    view_screen = _lines(export / "project-issue-type-view-screens.jsonl")[0]
    assert view_screen["source"]["id"] == "10000:1"
    assert view_screen["view_screen"]["screen_id"] == "7"
    assert view_screen["view_screen"]["shared_with_project_ids"] == ["10000"]
    coverage = json.loads((export / "manifest.json").read_text())["coverage"]
    assert coverage["project_issue_type_view_screens"] == {
        "status": "complete",
        "count": 1,
        "api": "internal",
    }
    for stage in (
        "projects",
        "issue_types",
        "issue_type_schemes",
        "project_workflow_schemes",
    ):
        assert coverage[stage] == {"status": "complete", "count": 1}


def test_failed_project_inventory_blocks_project_workflow_schemes(
    tmp_path: Path, jira_basic_auth_fixture: FixtureSetup
) -> None:
    jira_basic_auth_fixture(
        "https://jira.example.com", _handler(projects_status=403), defaults=False
    )

    assert cli.main(["export", "export"]) == 1

    coverage = json.loads((tmp_path / "export" / "manifest.json").read_text())[
        "coverage"
    ]
    assert coverage["projects"]["status"] == "unauthorized"
    assert coverage["project_issue_type_view_screens"] == {
        "status": "not_queried",
        "reason": "projects incomplete",
    }
    assert coverage["project_workflow_schemes"] == {
        "status": "not_queried",
        "reason": "projects incomplete",
    }
    assert not (tmp_path / "export" / "project-workflow-schemes.jsonl").exists()
