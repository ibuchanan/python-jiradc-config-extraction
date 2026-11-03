"""Jira extraction methods own REST paths and normalize API responses."""

import httpx
import pytest

from jira import JiraClient, JiraError


@pytest.mark.anyio
async def test_field_inventory_uses_jira_api_and_redacts_names() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {"id": "summary", "name": "Summary", "custom": False},
                {"id": "customfield_2", "name": "password: private", "custom": True},
                {"id": "customfield_1", "name": "Team", "custom": True},
            ],
        )

    async with JiraClient(
        "https://jira.example.com/jira",
        user="admin",
        password="secret",
        transport=httpx.MockTransport(handler),
    ) as client:
        records = await client.field_inventory()

    assert [record["source"]["id"] for record in records] == [
        "customfield_1",
        "customfield_2",
    ]
    assert [record["field"]["name"] for record in records] == [
        "Team",
        "[REDACTED]",
    ]
    assert requests[0].url.path == "/jira/rest/api/2/field"


@pytest.mark.anyio
async def test_jira_api_error_does_not_expose_response_body() -> None:
    async with JiraClient(
        "https://jira.example.com",
        user="admin",
        password="secret",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(403, json={"errorMessages": ["secret"]})
        ),
    ) as client:
        with pytest.raises(JiraError) as error:
            await client.field_inventory()

    assert error.value.status_code == httpx.codes.FORBIDDEN
    assert error.value.stage_status == "unauthorized"
    assert error.value.error_type == "HTTPStatusError"
    assert "secret" not in str(error.value)
