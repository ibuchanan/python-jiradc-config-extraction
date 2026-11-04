"""Per-stage record order matches the documented order table."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest

from jira import JiraClient, ScreenTab, field_app_associations

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
async def test_custom_fields_keep_page_order_and_sort_within_a_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["startAt"])
        values = (
            [{"id": "customfield_3"}, {"id": "customfield_1"}]
            if start == 0
            else [{"id": "customfield_2"}]
        )
        return httpx.Response(200, json={"values": values, "total": 3})

    async with _client(handler) as client:
        records = await _records(
            client.custom_fields(["customfield_1", "customfield_2", "customfield_3"])
        )

    assert [record["source"]["id"] for record in records] == [
        "customfield_1",
        "customfield_3",
        "customfield_2",
    ]


@pytest.mark.anyio
async def test_options_follow_field_order_and_sort_within_a_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        field = request.url.path.split("/")[6]
        options = [{"id": 2}, {"id": 1}] if field == "1" else [{"id": 8}, {"id": 7}]
        return httpx.Response(200, json={"options": options, "total": 2})

    async with _client(handler) as client:
        records = await _records(
            client.custom_field_options(["customfield_1", "customfield_2"])
        )

    assert [record["source"]["id"] for record in records] == [
        "customfield_1:1",
        "customfield_1:2",
        "customfield_2:7",
        "customfield_2:8",
    ]


@pytest.mark.anyio
async def test_issue_types_are_sorted_by_numeric_id() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": "2"}, {"id": "1"}])

    async with _client(handler) as client:
        records = await _records(client.issue_types())

    assert [record["source"]["id"] for record in records] == ["1", "2"]


@pytest.mark.anyio
async def test_screens_sort_by_numeric_id_within_a_page() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"screens": [{"id": 11}, {"id": 10}], "total": 2},
        )

    async with _client(handler) as client:
        records = await _records(client.screens())

    assert [record["source"]["id"] for record in records] == ["10", "11"]


@pytest.mark.anyio
async def test_screen_tabs_keep_jira_response_order() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 6}, {"id": 5}])

    async with _client(handler) as client:
        records = await _records(client.screen_tabs(["10"]))

    assert [record["source"]["id"] for record in records] == ["10:6", "10:5"]


@pytest.mark.anyio
async def test_screen_tab_fields_keep_jira_response_order() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"id": "customfield_9"}, {"id": "customfield_1"}]
        )

    async with _client(handler) as client:
        records = await _records(client.screen_tab_fields([ScreenTab("10", "5")]))

    assert [record["source"]["id"] for record in records] == [
        "10:5:customfield_9",
        "10:5:customfield_1",
    ]


def test_associations_are_sorted_by_source_id() -> None:
    def field(identifier: str) -> dict[str, Any]:
        return {
            "source": {"id": identifier, "endpoint": "/rest/api/2/field"},
            "raw": {"schema": {"custom": "com.example.app:type"}},
        }

    app = {
        "source": {"id": "com.example.app", "endpoint": "/rest/plugins/1.0/"},
        "app": {"name": "Example", "version": "1.0"},
        "raw": {"key": "com.example.app"},
    }

    records = field_app_associations(
        [field("customfield_2"), field("customfield_1")], [app]
    )

    assert [record["source"]["id"] for record in records] == [
        "customfield_1:com.example.app",
        "customfield_2:com.example.app",
    ]
