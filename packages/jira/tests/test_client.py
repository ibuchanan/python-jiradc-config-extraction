"""Tests for the Jira HTTP client."""

from __future__ import annotations

from base64 import b64encode
from datetime import timedelta

import httpx
import pytest
from httpx_limiter import Rate

from jira.client import JiraClient, SpacingRateLimiter, _retry_after_seconds


@pytest.mark.anyio
async def test_get_preserves_context_path_and_sends_basic_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    async with JiraClient(
        "https://jira.example.com/jira/",
        user="admin",
        password="secret",
        requests_per_minute=60_000,
        transport=httpx.MockTransport(handler),
    ) as client:
        response = await client.call("getCustomFields", startAt=0)

    assert response.json() == {"ok": True}
    assert (
        str(requests[0].url)
        == "https://jira.example.com/jira/rest/api/2/customFields?startAt=0"
    )
    assert requests[0].headers["Authorization"].startswith("Basic ")


@pytest.mark.anyio
async def test_get_uses_basic_auth_with_user_and_password() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    async with JiraClient(
        "https://jira.example.com",
        user="admin",
        password="private-password",
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.call("getFields")

    expected = b64encode(b"admin:private-password").decode("ascii")
    assert requests[0].headers["Authorization"] == f"Basic {expected}"
    assert "private-password" not in str(requests[0].url)


@pytest.mark.anyio
async def test_get_retries_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(200, json={"ok": True}),
    ]
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return responses.pop(0)

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("jira.client.asyncio.sleep", fake_sleep)
    async with JiraClient(
        "https://jira.example.com",
        user="admin",
        password="secret",
        requests_per_minute=60_000,
        transport=httpx.MockTransport(handler),
    ) as client:
        response = await client.call("getFields")

    expected_requests = 2
    retry_delay = 2.0
    assert response.status_code == httpx.codes.OK
    assert request_count == expected_requests
    assert sleeps[0] == retry_delay


@pytest.mark.anyio
async def test_get_stops_after_max_retries() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"})

    async with JiraClient(
        "https://jira.example.com",
        user="admin",
        password="secret",
        requests_per_minute=60_000,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.call("getFields")


@pytest.mark.anyio
async def test_call_rejects_mutating_openapi_operation() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    async with JiraClient(
        "https://jira.example.com",
        user="admin",
        password="secret",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ValueError, match="Unsupported read-only"):
            await client.call("createIssue", summary="unsafe")
    assert requests == []


def test_retry_after_parses_seconds_and_invalid_values() -> None:
    assert _retry_after_seconds("1.5") == pytest.approx(1.5)
    assert _retry_after_seconds("bad") == pytest.approx(60.0)


@pytest.mark.anyio
async def test_spacing_limiter_spaces_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = SpacingRateLimiter(Rate.create(2, timedelta(seconds=1)))
    now = 10.0
    waits: list[float] = []

    class Clock:
        def time(self) -> float:
            return now

    async def fake_sleep(seconds: float) -> None:
        nonlocal now
        waits.append(seconds)
        now += seconds

    monkeypatch.setattr("jira.client.asyncio.get_running_loop", lambda: Clock())
    monkeypatch.setattr("jira.client.asyncio.sleep", fake_sleep)
    async with limiter:
        pass
    async with limiter:
        pass

    assert waits == [0.5]
