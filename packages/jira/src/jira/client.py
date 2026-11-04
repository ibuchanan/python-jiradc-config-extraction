"""Async, rate-limited Jira Data Center HTTP client."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from importlib.resources import files
from typing import TYPE_CHECKING, Any

import httpx
from httpx_limiter import AsyncRateLimitedTransport, Rate
from httpx_limiter.abstract_async_limiter import AbstractAsyncLimiter
from openapiclient import OpenAPIClient

from . import extraction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

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
        "getAllProjects",
        "getIssueAllTypes",
        "getAllIssueTypeSchemes",
        "getIssueTypeScheme",
        "getAssociatedProjects",
        "getWorkflowSchemeForProject",
        "getProjectConfigIssueTypeFields",
    }
)
_WIRE_BODY_HEADERS = frozenset(
    {"content-encoding", "content-length", "transfer-encoding"}
)
logger = logging.getLogger(__name__)


class _JsonErrorBodies(httpx.AsyncBaseTransport):
    """Make HTTP error responses parse as JSON so their status is not hidden.

    The OpenAPI client parses every body as JSON before checking the status, so a
    plain-text error such as Jira's 400 parameter message would surface as an
    unrelated JSON decode error. Non-JSON error bodies are replaced with a JSON
    placeholder (dropping the text), which lets ``call()`` raise on the status.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self._transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._transport.handle_async_request(request)
        if response.status_code < httpx.codes.BAD_REQUEST:
            return response
        body = await response.aread()
        try:
            json.loads(body)
        except ValueError:
            await response.aclose()
            headers = _without_wire_body_headers(response.headers)
            headers.pop("content-type", None)  # replaced by the JSON placeholder
            return httpx.Response(
                response.status_code,
                json={"message": "Jira returned a non-JSON error body"},
                headers=headers,
                request=request,
            )
        return httpx.Response(
            response.status_code,
            content=body,
            headers=_without_wire_body_headers(response.headers),
            request=request,
        )

    async def aclose(self) -> None:
        await self._transport.aclose()


def _declare_undocumented_api(definition: dict[str, Any]) -> None:
    """Declare what Jira DC serves but the bundled spec omits.

    The OpenAPI client calls only declared operations and parameters:

    * On a single issue type scheme, ``expand=issueTypes,defaultIssueType`` is what
      makes Jira return the scheme's issue types and its default issue type.
    * ``/rest/projectconfig/1/issuetype/{projectKey}/{issueTypeId}/fields`` belongs
      to Jira's internal project-settings API, not the documented REST API, so it
      may change between versions. It reports the screen a project's issue type
      uses for viewing an issue.
    """
    paths = definition["paths"]
    operation = paths["/api/2/issuetypescheme/{schemeId}"]["get"]
    operation.setdefault("parameters", []).append(
        {"name": "expand", "in": "query", "schema": {"type": "string"}}
    )
    paths["/projectconfig/1/issuetype/{projectKey}/{issueTypeId}/fields"] = {
        "get": {
            "operationId": "getProjectConfigIssueTypeFields",
            "parameters": [
                {
                    "name": name,
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
                for name in ("projectKey", "issueTypeId")
            ],
            "responses": {"200": {"description": "OK"}},
        }
    }


def _without_wire_body_headers(headers: httpx.Headers) -> dict[str, str]:
    """Drop headers that describe the original, possibly compressed, wire body."""
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in _WIRE_BODY_HEADERS
    }


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
    """Read-only Jira Data Center client with pacing and bounded 429 retries.

    ``on_rate_limit`` is called with the number of seconds about to be waited each
    time Jira answers 429, before the wait starts.
    """

    def __init__(  # noqa: PLR0913 -- keyword-only options of one client
        self,
        base_url: str,
        *,
        user: str,
        password: str,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
        transport: httpx.AsyncBaseTransport | None = None,
        on_rate_limit: Callable[[float], None] | None = None,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be greater than zero")
        self.base_url = base_url.rstrip("/")
        self._on_rate_limit = on_rate_limit
        limiter = SpacingRateLimiter(
            Rate.create(requests_per_minute, timedelta(minutes=1))
        )
        rate_limited_transport = AsyncRateLimitedTransport(
            limiter=limiter,
            transport=_JsonErrorBodies(transport or httpx.AsyncHTTPTransport()),
        )
        definition = json.loads(
            files("jira")
            .joinpath("jira_software_dc_11.2.1_swagger.v3.json")
            .read_text(encoding="utf-8")
        )
        definition["servers"] = [{"url": f"{self.base_url}/rest"}]
        _declare_undocumented_api(definition)
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
            # `data` is already decoded; keeping wire-body headers such as
            # Content-Encoding would make httpx decompress plain JSON again.
            response = httpx.Response(
                result["status"],
                json=result["data"],
                headers=_without_wire_body_headers(result["headers"]),
                request=httpx.Request("GET", self.base_url),
            )
            if response.status_code != httpx.codes.TOO_MANY_REQUESTS:
                response.raise_for_status()
                return response
            if attempt == MAX_RETRIES:
                response.raise_for_status()
            delay = _retry_after_seconds(response.headers.get("Retry-After"))
            logger.debug("Jira returned 429; retrying after %.1f seconds", delay)
            if self._on_rate_limit is not None:
                self._on_rate_limit(delay)
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

    def custom_fields(
        self, field_ids: list[str], start_at: int = 0
    ) -> AsyncIterator[extraction.Batch]:
        """Yield custom fields per page, with their scope; resume with ``start_at``."""
        return extraction.custom_fields(self, field_ids, start_at)

    def custom_field_options(
        self, field_ids: list[str], start_at: int = 0
    ) -> AsyncIterator[extraction.Batch]:
        """Yield option records per custom field; ``start_at`` is a field index."""
        return extraction.custom_field_options(self, field_ids, start_at)

    def screens(self, start_at: int = 0) -> AsyncIterator[extraction.Batch]:
        """Yield screen records per page; resume with ``start_at``."""
        return extraction.screens(self, start_at)

    def screen_tabs(
        self, screen_ids: list[str], start_at: int = 0
    ) -> AsyncIterator[extraction.Batch]:
        """Yield tab records per screen; ``start_at`` is a screen index."""
        return extraction.screen_tabs(self, screen_ids, start_at)

    def screen_tab_fields(
        self, tabs: list[extraction.ScreenTab], start_at: int = 0
    ) -> AsyncIterator[extraction.Batch]:
        """Yield the fields on each tab; ``start_at`` is a tab index."""
        return extraction.screen_tab_fields(self, tabs, start_at)

    def projects(self, start_at: int = 0) -> AsyncIterator[extraction.Batch]:
        """Yield the project inventory as one batch; ``start_at`` 1 means done."""
        return extraction.projects(self, start_at)

    def issue_types(self, start_at: int = 0) -> AsyncIterator[extraction.Batch]:
        """Yield the issue type inventory as one batch; ``start_at`` 1 means done."""
        return extraction.issue_types(self, start_at)

    def issue_type_schemes(self, start_at: int = 0) -> AsyncIterator[extraction.Batch]:
        """Yield one record per issue type scheme; ``start_at`` is a scheme index."""
        return extraction.issue_type_schemes(self, start_at)

    def project_issue_type_view_screens(
        self, targets: list[extraction.IssueTypeScreenTarget], start_at: int = 0
    ) -> AsyncIterator[extraction.Batch]:
        """Yield each project issue type's view screen; ``start_at`` is an index."""
        return extraction.project_issue_type_view_screens(self, targets, start_at)

    def project_workflow_schemes(
        self, projects: list[extraction.ProjectRef], start_at: int = 0
    ) -> AsyncIterator[extraction.Batch]:
        """Yield each project's workflow scheme; ``start_at`` is a project index."""
        return extraction.project_workflow_schemes(self, projects, start_at)


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
