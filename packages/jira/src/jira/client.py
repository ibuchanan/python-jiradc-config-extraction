"""Async, rate-limited Jira Data Center HTTP client."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from importlib.resources import files
from typing import Any

import httpx
from httpx_limiter import AsyncRateLimitedTransport, Rate
from httpx_limiter.abstract_async_limiter import AbstractAsyncLimiter
from openapiclient import OpenAPIClient

from . import extraction

DEFAULT_REQUESTS_PER_MINUTE = 60
MAX_RETRIES = 3
READ_OPERATIONS = frozenset(
    {
        "getFields",
        "getServerInfo",
        "getCustomFields",
        "getCustomFieldOptions",
        "getAllScreens",
        "getAllTabs",
        "getAllFields",
    }
)
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
        *,
        user: str,
        password: str,
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
        definition = json.loads(
            files("jira")
            .joinpath("jira_software_dc_11.2.1_swagger.v3.json")
            .read_text(encoding="utf-8")
        )
        definition["servers"] = [{"url": f"{self.base_url}/rest"}]
        self._api = OpenAPIClient(definition=definition)
        self._operations = self._api.AsyncClient(
            auth=httpx.BasicAuth(user, password),
            headers={"Accept": "application/json"},
            timeout=30.0,
            transport=rate_limited_transport,
        )

    async def __aenter__(self) -> JiraClient:
        await self._operations.__aenter__()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._operations.__aexit__(None, None, None)

    async def call(
        self, operation: str, **parameters: str | int | bool
    ) -> httpx.Response:
        """Call a supported read-only OpenAPI operation with bounded 429 retries."""
        if operation not in READ_OPERATIONS:
            raise ValueError("Unsupported read-only Jira operation")
        for attempt in range(MAX_RETRIES + 1):
            result = await self._operations[operation](**parameters)
            response = httpx.Response(
                result["status"],
                json=result["data"],
                headers=result["headers"],
                request=httpx.Request("GET", self.base_url),
            )
            if response.status_code != httpx.codes.TOO_MANY_REQUESTS:
                response.raise_for_status()
                return response
            if attempt == MAX_RETRIES:
                response.raise_for_status()
            delay = _retry_after_seconds(response.headers.get("Retry-After"))
            logger.warning("Jira returned 429; retrying after %.1f seconds", delay)
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def field_inventory(self) -> list[dict[str, Any]]:
        """Extract custom field inventory.

        Returns list of dicts with schema_version, source, extracted_at, and field data.
        Raises JiraError on validation or HTTP errors.
        """
        return await extraction.field_inventory(self)

    async def server_version(self) -> str | None:
        """Extract Jira server version.

        Returns version string or None on error or if endpoint unavailable.
        Raises JiraError on non-404 HTTP errors.
        """
        return await extraction.server_version(self)

    async def field_applicability(self, field_ids: list[str]) -> list[dict[str, Any]]:
        """Extract field applicability data for given field IDs.

        Returns list of normalized records.
        Raises JiraError on validation or HTTP errors.
        """
        return await extraction.field_applicability(self, field_ids)

    async def field_options(self, field_ids: list[str]) -> list[dict[str, Any]]:
        """Extract field options for given custom field IDs.

        Returns list of normalized records.
        Raises JiraError on validation or HTTP errors.
        """
        return await extraction.field_options(self, field_ids)

    async def screens(self) -> list[dict[str, Any]]:
        """Extract screens.

        Returns list of normalized records.
        Raises JiraError on validation or HTTP errors.
        """
        return await extraction.screens(self)

    async def screen_tabs(self, screen_ids: list[str]) -> list[dict[str, Any]]:
        """Extract screen tabs for given screen IDs.

        Returns list of normalized records.
        Raises JiraError on validation or HTTP errors.
        """
        return await extraction.screen_tabs(self, screen_ids)

    async def screen_placements(
        self, tabs: list[tuple[str, str]]
    ) -> list[dict[str, Any]]:
        """Extract screen field placements for given (screen_id, tab_id) tuples.

        Args:
            tabs: List of (screen_id, tab_id) tuples.

        Returns list of normalized records sorted by (screen_id, tab_id, position).
        Raises JiraError on validation or HTTP errors.
        """
        return await extraction.screen_placements(self, tabs)


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
