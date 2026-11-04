"""Jira extraction methods own REST paths and normalize API responses."""

from __future__ import annotations

import gzip
import json
from typing import Any

import httpx
import pytest

from jira import (
    JiraClient,
    JiraError,
    describe_exception,
    field_app_associations,
)


@pytest.mark.anyio
async def test_field_inventory_uses_jira_api_and_keeps_names_verbatim() -> None:
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
        "password: private",
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


@pytest.mark.anyio
async def test_transport_failure_reports_safe_cause_chain() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] unknown issuer")

    async with JiraClient(
        "https://jira.example.com/jira",
        user="admin",
        password="s3cret",  # noqa: S106
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(JiraError) as caught:
            await client.field_inventory()

    assert caught.value.error_type == "RequestError"
    assert caught.value.detail is not None
    assert "ConnectError" in caught.value.detail
    assert "s3cret" not in caught.value.detail


def test_field_app_associations_match_exact_app_keys_only() -> None:
    field = {
        "source": {
            "system": "jira",
            "entity": "field",
            "id": "customfield_1",
            "endpoint": "/rest/api/2/field",
        },
        "raw": {
            "id": "customfield_1",
            "schema": {"custom": "com.example.app:type", "type": "string"},
        },
    }
    app = {
        "source": {
            "system": "jira",
            "entity": "app",
            "id": "com.example.app",
            "endpoint": "/rest/plugins/1.0/",
        },
        "app": {"name": "Example App", "version": "1.2.3"},
        "raw": {"key": "com.example.app", "license": "kept"},
    }

    records = field_app_associations([field], [app])

    assert [record["source"]["id"] for record in records] == [
        "customfield_1:com.example.app"
    ]
    assert records[0]["raw"] == {"field": field["raw"], "app": app["raw"]}
    assert records[0]["evidence"] == {
        "field_type_key": "com.example.app:type",
        "app_key": "com.example.app",
        "app_endpoint": "/rest/plugins/1.0/",
        "match": "field schema.custom prefix equals installed app key",
    }
    assert field_app_associations([field], []) == []
    assert (
        field_app_associations(
            [{"source": {"id": "x"}, "raw": {"schema": {"custom": "other:type"}}}],
            [app],
        )
        == []
    )


def test_describe_exception_includes_os_message_but_not_other_messages() -> None:
    os_error = OSError("[Errno 8] nodename nor servname provided")
    wrapped = RuntimeError("body said password=hunter2")
    wrapped.__cause__ = os_error
    other = ValueError("body said password=hunter2")

    assert describe_exception(wrapped) == (
        "RuntimeError <- OSError: [Errno 8] nodename nor servname provided"
    )
    assert describe_exception(other) == "ValueError"


@pytest.mark.anyio
async def test_field_inventory_accepts_compressed_responses() -> None:
    body = json.dumps([{"id": "customfield_1", "name": "Team", "custom": True}])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=gzip.compress(body.encode()),
            headers={"Content-Encoding": "gzip", "Content-Type": "application/json"},
        )

    async with JiraClient(
        "https://jira.example.com/jira",
        user="admin",
        password="s3cret",  # noqa: S106
        requests_per_minute=60_000,
        transport=httpx.MockTransport(handler),
    ) as client:
        records = await client.field_inventory()

    assert [record["source"]["id"] for record in records] == ["customfield_1"]


@pytest.mark.anyio
async def test_installed_apps_records_user_installed_plugins_with_full_raw() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "plugins": [
                    {
                        "key": "com.example.app",
                        "name": "Example App",
                        "version": "1.2.3",
                        "userInstalled": True,
                        "sentinel": "kept",
                    },
                    {
                        "key": "com.atlassian.jira",
                        "name": "Jira Core",
                        "version": "11.3.12",
                        "userInstalled": False,
                    },
                    {"key": "no-version", "name": "No Version", "userInstalled": True},
                ],
                "total": 3,
            },
        )

    async with JiraClient(
        "https://jira.example.com/jira",
        user="admin",
        password="s3cret",  # noqa: S106
        requests_per_minute=60_000,
        transport=httpx.MockTransport(handler),
    ) as client:
        records: list[dict[str, Any]] = [
            record
            async for batch in client.installed_apps()
            for record in batch.records
        ]

    assert [record["source"]["id"] for record in records] == [
        "com.example.app",
        "no-version",
    ]
    assert records[0]["raw"]["sentinel"] == "kept"
    assert records[0]["app"]["version"] == "1.2.3"
    assert records[1]["app"]["version"] is None
    assert requests[0].url.path == "/jira/rest/plugins/1.0/"


@pytest.mark.anyio
async def test_malformed_screens_page_reports_which_validation_failed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    async with JiraClient(
        "https://jira.example.com/jira",
        user="admin",
        password="s3cret",  # noqa: S106
        requests_per_minute=60_000,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(JiraError) as caught:
            [batch async for batch in client.screens()]

    assert caught.value.detail == "Invalid screens page"


@pytest.mark.anyio
async def test_plain_text_error_body_reports_http_status() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            content=b'"startAt" and "maxResults" parameters must be numeric',
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )

    async with JiraClient(
        "https://jira.example.com/jira",
        user="admin",
        password="s3cret",  # noqa: S106
        requests_per_minute=60_000,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(JiraError) as caught:
            [batch async for batch in client.screens()]

    assert caught.value.status_code == httpx.codes.BAD_REQUEST
    assert caught.value.error_type == "HTTPStatusError"


@pytest.mark.anyio
async def test_screens_sends_both_paging_parameters_and_reads_screens_key() -> None:
    queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "startAt": 0,
                "maxResults": 100,
                "total": 1,
                "screens": [{"id": 10, "name": "Default Screen"}],
            },
        )

    async with JiraClient(
        "https://jira.example.com/jira",
        user="admin",
        password="s3cret",  # noqa: S106
        requests_per_minute=60_000,
        transport=httpx.MockTransport(handler),
    ) as client:
        records: list[dict[str, Any]] = [
            record async for batch in client.screens() for record in batch.records
        ]

    assert queries == [{"startAt": "0", "maxResults": "100"}]
    assert [record["source"]["id"] for record in records] == ["10"]


@pytest.mark.anyio
async def test_screens_keep_first_repeated_source_id_in_a_page() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "startAt": 0,
                "maxResults": 100,
                "total": 2,
                "screens": [
                    {"id": 10, "name": "First"},
                    {"id": 10, "name": "Second"},
                ],
            },
        )

    async with JiraClient(
        "https://jira.example.com/jira",
        user="admin",
        password="s3cret",  # noqa: S106
        requests_per_minute=60_000,
        transport=httpx.MockTransport(handler),
    ) as client:
        records: list[dict[str, Any]] = [
            record async for batch in client.screens() for record in batch.records
        ]

    assert [
        (record["source"]["id"], record["screen"]["name"]) for record in records
    ] == [("10", "First")]
