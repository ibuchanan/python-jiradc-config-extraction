"""Every record keeps the complete source response under ``raw``.

``docs/reference/cli.md`` documents that ``raw`` holds the complete source
response. These fixtures exercise every record builder so the claim cannot
drift as builders change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest

from jira import IssueTypeScreenTarget, JiraClient, ProjectRef, ScreenTab

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


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path.removeprefix("/jira/rest")
    responses: dict[str, object] = {
        "/api/2/field": [{"id": "customfield_1", "custom": True, "sentinel": "field"}],
        "/api/2/customFields": {
            "values": [
                {
                    "id": "customfield_1",
                    "projectIds": [],
                    "issueTypeIds": [],
                    "sentinel": "custom-fields",
                }
            ],
            "total": 1,
        },
        "/api/2/customFields/1/options": {
            "options": [{"id": 1, "sentinel": "option"}],
            "total": 1,
        },
        "/api/2/screens": {
            "screens": [{"id": 10, "sentinel": "screen"}],
            "total": 1,
        },
        "/api/2/screens/10/tabs": [{"id": 20, "sentinel": "tab"}],
        "/api/2/screens/10/tabs/20/fields": [
            {"id": "customfield_1", "sentinel": "tab-field"}
        ],
        "/api/2/project": [{"id": "1", "key": "A", "sentinel": "project"}],
        "/api/2/issuetype": [{"id": "2", "sentinel": "issue-type"}],
        "/api/2/issuetypescheme": {"schemes": [{"id": "3"}]},
        "/api/2/issuetypescheme/3": {
            "id": "3",
            "issueTypes": [],
            "sentinel": "scheme",
        },
        "/api/2/issuetypescheme/3/associations": [
            {"id": "1", "sentinel": "association"}
        ],
        "/api/2/project/A/workflowscheme": {"id": 7, "sentinel": "workflow-scheme"},
        "/plugins/1.0/": {
            "plugins": [
                {
                    "key": "com.example.app",
                    "userInstalled": True,
                    "version": "1.0",
                    "sentinel": "app",
                }
            ]
        },
        "/projectconfig/1/issuetype/A/2/fields": {
            "viewScreen": {"screenId": 7, "sentinel": "view"},
            "createScreen": {"sentinel": "create"},
            "editScreen": {"sentinel": "edit"},
            "sentinel": "response",
        },
    }
    if path not in responses:
        raise AssertionError(path)
    return httpx.Response(200, json=responses[path])


@pytest.mark.anyio
async def test_every_record_builder_keeps_the_complete_source_payload() -> None:
    async with _client(_handler) as client:
        single_response_records = {
            "field": await client.field_inventory(),
            "custom_fields": await _records(client.custom_fields(["customfield_1"])),
            "custom_field_options": await _records(
                client.custom_field_options(["customfield_1"])
            ),
            "screen": await _records(client.screens()),
            "screen_tab": await _records(client.screen_tabs(["10"])),
            "screen_tab_field": await _records(
                client.screen_tab_fields([ScreenTab("10", "20")])
            ),
            "project": await _records(client.projects()),
            "issue_type": await _records(client.issue_types()),
            "project_workflow_scheme": await _records(
                client.project_workflow_schemes([ProjectRef("1", "A")])
            ),
            "app": await _records(client.installed_apps()),
        }
        schemes = await _records(client.issue_type_schemes())
        view_screens = await _records(
            client.project_issue_type_view_screens(
                [IssueTypeScreenTarget(ProjectRef("1", "A"), "2")]
            )
        )

    sentinels = {
        "field": "field",
        "custom_fields": "custom-fields",
        "custom_field_options": "option",
        "screen": "screen",
        "screen_tab": "tab",
        "screen_tab_field": "tab-field",
        "project": "project",
        "issue_type": "issue-type",
        "project_workflow_scheme": "workflow-scheme",
        "app": "app",
    }
    for entity, sentinel in sentinels.items():
        assert single_response_records[entity][0]["raw"]["sentinel"] == sentinel

    assert schemes[0]["raw"]["scheme"]["sentinel"] == "scheme"
    assert schemes[0]["raw"]["associations"][0]["sentinel"] == "association"

    assert view_screens[0]["raw"] == {
        "viewScreen": {"screenId": 7, "sentinel": "view"},
        "createScreen": {"sentinel": "create"},
        "editScreen": {"sentinel": "edit"},
        "sentinel": "response",
    }
