"""Async, rate-limited Jira Data Center HTTP client."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx
from httpx_limiter import AsyncRateLimitedTransport, Rate
from httpx_limiter.abstract_async_limiter import AbstractAsyncLimiter

DEFAULT_REQUESTS_PER_MINUTE = 60
MAX_RETRIES = 3
logger = logging.getLogger(__name__)


class SpacingRateLimiter(AbstractAsyncLimiter):
    """Space requests evenly across the configured window instead of bursting."""

    def __init__(self, rate: Rate) -> None:
        if rate.magnitude <= 0:
            raise ValueError("rate magnitude must be greater than zero")
        self._interval = rate.in_seconds() / rate.magnitude
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def __aenter__(self) -> SpacingRateLimiter:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            wait = self._next_allowed - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = asyncio.get_running_loop().time()
            self._next_allowed = now + self._interval
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        return None


class JiraClient:
    """Read-only Jira Data Center client with pacing and bounded 429 retries."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be greater than zero")
        self.base_url = base_url.rstrip("/")
        limiter = SpacingRateLimiter(
            Rate.create(requests_per_minute, timedelta(minutes=1))
        )
        rate_limited_transport = AsyncRateLimitedTransport(
            limiter=limiter,
            transport=transport or httpx.AsyncHTTPTransport(),
        )
        self._http = httpx.AsyncClient(
            base_url=f"{self.base_url}/",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30.0,
            transport=rate_limited_transport,
        )

    async def __aenter__(self) -> JiraClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(
        self,
        path: str,
        *,
        params: dict[str, str | int | float | bool | None] | None = None,
    ) -> httpx.Response:
        """GET a Jira REST path, retrying 429 responses up to a fixed limit."""
        relative_path = path.lstrip("/")
        for attempt in range(MAX_RETRIES + 1):
            response = await self._http.get(relative_path, params=params)
            if response.status_code != httpx.codes.TOO_MANY_REQUESTS:
                response.raise_for_status()
                return response
            if attempt == MAX_RETRIES:
                response.raise_for_status()
            delay = _retry_after_seconds(response.headers.get("Retry-After"))
            logger.warning("Jira returned 429; retrying after %.1f seconds", delay)
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")


def _retry_after_seconds(value: str | None) -> float:
    if value is None:
        return 60.0
    try:
        return max(float(value), 0.0)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except TypeError, ValueError:
            return 60.0
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max((retry_at - datetime.now(tz=UTC)).total_seconds(), 0.0)
