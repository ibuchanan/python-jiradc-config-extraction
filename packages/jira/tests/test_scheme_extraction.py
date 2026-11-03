"""Project, issue type, issue type scheme, and workflow scheme extraction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest

from jira import JiraClient, JiraError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from jira import Batch


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> JiraClient:
    return JiraClient(
        "https://jira.example.com/jira",
        user="admin",
        password="s3cret",  # noqa: S106
        requests_per_minute=60_000,
        transport=httpx.MockTransport(handler),
    )


async def _records(batches: AsyncIterator[Batch]) -> list[dict[str, Any]]:
    return [record async for batch in batches for record in batch.records]


@pytest.mark.anyio
async def test_projects_include_archived_and_are_ordered_by_numeric_id() -> None:
    queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(dict(request.url.params))
        return httpx.Response(
            200,
            json=[
                {"id": "10010", "key": "OLD", "name": "Old", "archived": True},
                {"id": "9999", "key": "NEW", "name": "password: x", "archived": False},
            ],
        )

    async with _client(handler) as client:
        records = await _records(client.projects())

    assert queries == [{"includeArchived": "true"}]
    assert [record["source"]["id"] for record in records] == ["9999", "10010"]
    assert records[0]["project"]["name"] == "password: x"
    assert records[1]["project"] == {
        "key": "OLD",
        "name": "Old",
        "project_type_key": None,
        "archived": True,
    }


@pytest.mark.anyio
async def test_single_request_stages_do_nothing_once_committed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call Jira")

    async with _client(handler) as client:
        assert await _records(client.projects(start_at=1)) == []
        assert await _records(client.issue_types(start_at=1)) == []


@pytest.mark.anyio
async def test_issue_types_keep_id_name_and_subtask_only() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": "1", "name": "Bug", "subtask": False, "description": "free text"}
            ],
        )

    async with _client(handler) as client:
        records = await _records(client.issue_types())

    assert records[0]["issue_type"] == {"name": "Bug", "subtask": False}


def _scheme_handler(requests: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append(path.removeprefix("/jira/rest/api/2"))
        if path.endswith("/issuetypescheme"):
            return httpx.Response(
                200, json={"schemes": [{"id": "10200"}, {"id": "10100"}]}
            )
        scheme_id = path.split("/")[6]
        if path.endswith("/associations"):
            return httpx.Response(
                200,
                json=[{"id": "10000", "key": "A", "lead": {"name": "private-lead"}}]
                if scheme_id == "10100"
                else [],
            )
        assert request.url.params["expand"] == "issueTypes,defaultIssueType"
        return httpx.Response(
            200,
            json={
                "id": scheme_id,
                "name": f"Scheme {scheme_id}",
                "issueTypes": [{"id": "3"}, {"id": "1"}],
                "defaultIssueType": {"id": "1"},
            },
        )

    return handler


@pytest.mark.anyio
async def test_issue_type_schemes_combine_issue_types_and_projects() -> None:
    requests: list[str] = []

    async with _client(_scheme_handler(requests)) as client:
        records = await _records(client.issue_type_schemes())

    assert [record["source"]["id"] for record in records] == ["10100", "10200"]
    assert records[0]["issue_type_scheme"] == {
        "name": "Scheme 10100",
        "issue_type_ids": ["1", "3"],
        "default_issue_type_id": "1",
        "project_ids": ["10000"],
    }
    assert records[1]["issue_type_scheme"]["project_ids"] == []
    assert "private-lead" not in str(records)


@pytest.mark.anyio
async def test_issue_type_schemes_resume_at_the_saved_index() -> None:
    requests: list[str] = []

    async with _client(_scheme_handler(requests)) as client:
        records = await _records(client.issue_type_schemes(start_at=1))

    assert [record["source"]["id"] for record in records] == ["10200"]
    assert "/issuetypescheme/10100/associations" not in requests


@pytest.mark.anyio
async def test_workflow_schemes_are_keyed_by_project() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "id": 7,
                "name": "Shared scheme",
                "defaultWorkflow": "jira",
                "issueTypeMappings": {"2": "bug flow", "1": "task flow"},
                "issueTypes": {"1": {"name": "ignored"}},
                "draft": False,
            },
        )

    async with _client(handler) as client:
        records = await _records(client.project_workflow_schemes([("10000", "A")]))

    assert requests == ["/jira/rest/api/2/project/A/workflowscheme"]
    assert records[0]["source"]["id"] == "10000"
    assert records[0]["workflow_scheme_id"] == "7"
    assert records[0]["default_workflow"] == "jira"
    assert records[0]["issue_type_mappings"] == {"1": "task flow", "2": "bug flow"}
    assert "issueTypes" not in records[0]


@pytest.mark.anyio
async def test_malformed_workflow_mappings_report_what_failed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": 7, "issueTypeMappings": {"1": ["not", "a", "name"]}}
        )

    async with _client(handler) as client:
        with pytest.raises(JiraError) as caught:
            await _records(client.project_workflow_schemes([("10000", "A")]))

    assert caught.value.detail == "Invalid workflow scheme issue type mappings"


def _view_screen_handler(
    requests: list[str], *, screen: object = None
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "viewScreen": screen
                if screen is not None
                else {
                    "screenId": 7,
                    "screenName": "Shared screen",
                    "state": "ACTIVE",
                    "sharedWithProjects": [
                        {"id": 10001, "key": "B", "name": "private-name"},
                        {"id": 10000, "key": "A", "name": "private-name"},
                    ],
                    "sharedWithIssueTypes": ["3", "2"],
                    "totalProjectsCount": 3,
                    "hiddenProjectsCount": 1,
                    "canEdit": True,
                }
            },
        )

    return handler


@pytest.mark.anyio
async def test_view_screens_use_the_internal_route_and_keep_ids_and_raw_payload() -> (
    None
):
    requests: list[str] = []

    async with _client(_view_screen_handler(requests)) as client:
        records = await _records(
            client.project_issue_type_view_screens([("10000", "A", "2")])
        )

    assert requests == ["/jira/rest/projectconfig/1/issuetype/A/2/fields"]
    assert records[0]["source"]["id"] == "10000:2"
    assert records[0]["view_screen"] == {
        "screen_id": "7",
        "screen_name": "Shared screen",
        "state": "ACTIVE",
        "shared_with_project_ids": ["10000", "10001"],
        "shared_with_issue_type_ids": ["2", "3"],
        "total_projects_count": 3,
        "hidden_projects_count": 1,
    }
    assert records[0]["raw"]["sharedWithProjects"][0]["name"] == "private-name"


@pytest.mark.anyio
async def test_view_screens_resume_at_the_saved_index() -> None:
    requests: list[str] = []
    targets = [("10000", "A", "2"), ("10000", "A", "3")]

    async with _client(_view_screen_handler(requests)) as client:
        records = await _records(client.project_issue_type_view_screens(targets, 1))

    assert requests == ["/jira/rest/projectconfig/1/issuetype/A/3/fields"]
    assert [record["source"]["id"] for record in records] == ["10000:3"]


@pytest.mark.anyio
async def test_missing_view_screen_reports_what_failed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"somethingElse": {}})

    async with _client(handler) as client:
        with pytest.raises(JiraError) as caught:
            await _records(client.project_issue_type_view_screens([("1", "A", "2")]))

    assert caught.value.detail == "Invalid view screen"
