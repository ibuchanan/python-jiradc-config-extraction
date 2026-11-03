"""Jira API extraction for fields, applicability, options, screens, tabs, placements."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from .errors import JiraError

if TYPE_CHECKING:
    from .client import JiraClient

SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:password|passwd|token|secret|api[_-]?key|credential|authorization)\b\s*[:=]"
)

_UNAUTHORIZED_CODES = (401, 403)
_UNSUPPORTED_CODES = (404, 405, 501)


def _record(
    entity: str, identifier: str, endpoint: str, **data: object
) -> dict[str, object]:
    """Create a normalized extraction record."""
    return {
        "schema_version": 1,
        "source": {
            "system": "jira",
            "entity": entity,
            "id": identifier,
            "endpoint": endpoint,
        },
        "extracted_at": datetime.now(UTC).isoformat(),
        **data,
    }


def _map_http_error(exc: httpx.HTTPStatusError) -> JiraError:
    """Map HTTP status to JiraError with appropriate stage_status."""
    status_code = exc.response.status_code
    if status_code in _UNAUTHORIZED_CODES:
        stage_status = "unauthorized"
    elif status_code in _UNSUPPORTED_CODES:
        stage_status = "unsupported"
    else:
        stage_status = "failed"
    return JiraError(
        f"HTTP {status_code}",
        status_code=status_code,
        error_type="HTTPStatusError",
        stage_status=stage_status,
    )


async def field_inventory(client: JiraClient) -> list[dict[str, object]]:
    """Extract custom field inventory.

    Returns list of dicts with schema_version, source, extracted_at, and field data.
    Raises JiraError on validation or HTTP errors.
    """
    endpoint = "/rest/api/2/field"
    try:
        response = await client.call("getFields")
        fields = response.json()
    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise JiraError(
            "Network error",
            error_type="RequestError",
            stage_status="failed",
        ) from exc

    if not isinstance(fields, list):
        raise JiraError(
            "Jira field inventory was not a list",
            error_type="ValueError",
            stage_status="failed",
        )

    custom_fields = [
        field
        for field in fields
        if isinstance(field, dict) and field.get("custom") is True
    ]

    if any(
        not isinstance(field.get("id"), str) or not field["id"]
        for field in custom_fields
    ):
        raise JiraError(
            "Jira custom field missing a stable id",
            error_type="ValueError",
            stage_status="failed",
        )

    by_id = {field["id"]: field for field in custom_fields}
    custom_fields = [by_id[field_id] for field_id in sorted(by_id)]

    return [
        _record(
            "field",
            field["id"],
            endpoint,
            field={
                "name": (
                    "[REDACTED]"
                    if isinstance(field.get("name"), str)
                    and SECRET_ASSIGNMENT.search(field["name"])
                    else field.get("name")
                )
            },
        )
        for field in custom_fields
    ]


async def server_version(client: JiraClient) -> str | None:
    """Extract Jira server version.

    Returns version string or None on error.
    Raises JiraError on non-404 HTTP errors.
    """
    try:
        response = await client.call("getServerInfo")
        info = response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in _UNSUPPORTED_CODES:
            return None
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise JiraError(
            "Network error",
            error_type="RequestError",
            stage_status="failed",
        ) from exc

    if not isinstance(info, dict):
        return None

    version = info.get("version")
    return version if isinstance(version, str) else None


async def field_applicability(
    client: JiraClient, field_ids: list[str]
) -> list[dict[str, object]]:
    """Extract field applicability data for given field IDs.

    Returns list of normalized records.
    Raises JiraError on validation or HTTP errors.
    """
    endpoint = "/rest/api/2/customFields"
    by_id: dict[str, dict[str, object]] = {}
    field_id_set = set(field_ids)

    try:
        start = 0
        while True:
            response = await client.call("getCustomFields", startAt=start)
            payload = response.json()

            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("values"), list)
                or not isinstance(payload.get("total"), int)
            ):
                raise ValueError("Invalid custom fields page")

            values = payload["values"]
            for field in values:
                if not isinstance(field, dict) or not isinstance(field.get("id"), str):
                    raise ValueError("Invalid custom field identifier")
                if field["id"] in field_id_set:
                    by_id[field["id"]] = _record(
                        "field_applicability",
                        field["id"],
                        endpoint,
                        field_id=field["id"],
                        project_ids=sorted(
                            str(value) for value in field.get("projectIds", [])
                        ),
                        issue_type_ids=sorted(
                            str(value) for value in field.get("issueTypeIds", [])
                        ),
                        all_projects=field.get("isAllProjects"),
                    )

            start += len(values)
            if start >= payload["total"]:
                break
            if not values:
                raise ValueError("Empty custom fields page before total")

    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise JiraError(
            "Network error",
            error_type="RequestError",
            stage_status="failed",
        ) from exc
    except ValueError as exc:
        raise JiraError(
            str(exc),
            error_type="ValueError",
            stage_status="failed",
        ) from exc

    return [by_id[key] for key in sorted(by_id)]


async def field_options(
    client: JiraClient, field_ids: list[str]
) -> list[dict[str, object]]:
    """Extract field options for given custom field IDs.

    Returns list of normalized records.
    Raises JiraError on validation or HTTP errors.
    """
    by_id: dict[tuple[str, str], dict[str, object]] = {}

    try:
        for field_id in field_ids:
            if (
                not field_id.startswith("customfield_")
                or not field_id.removeprefix("customfield_").isdigit()
            ):
                raise ValueError("Invalid custom field id")

            num = field_id.removeprefix("customfield_")
            endpoint = f"/rest/api/2/customFields/{num}/options"
            page = 1
            seen = 0

            while True:
                response = await client.call(
                    "getCustomFieldOptions",
                    customFieldId=num,
                    page=page,
                    useAllContexts=True,
                )
                payload = response.json()

                if (
                    not isinstance(payload, dict)
                    or not isinstance(payload.get("options"), list)
                    or not isinstance(payload.get("total"), int)
                ):
                    raise ValueError("Invalid custom field options page")

                options = payload["options"]
                for option in options:
                    if not isinstance(option, dict) or not isinstance(
                        option.get("id"), int
                    ):
                        raise ValueError("Invalid custom field option identifier")

                    option_id = str(option["id"])
                    value = option.get("value")
                    by_id[field_id, option_id] = _record(
                        "field_option",
                        f"{field_id}:{option_id}",
                        endpoint,
                        field_id=field_id,
                        value=(
                            "[REDACTED]"
                            if isinstance(value, str)
                            and SECRET_ASSIGNMENT.search(value)
                            else value
                        ),
                        disabled=option.get("disabled"),
                        child_ids=[
                            str(child) for child in option.get("childrenIds", [])
                        ],
                    )

                seen += len(options)
                if seen >= payload["total"]:
                    break
                if not options:
                    raise ValueError("Empty options page before total")
                page += 1

    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise JiraError(
            "Network error",
            error_type="RequestError",
            stage_status="failed",
        ) from exc
    except ValueError as exc:
        raise JiraError(
            str(exc),
            error_type="ValueError",
            stage_status="failed",
        ) from exc

    return [by_id[key] for key in sorted(by_id)]


async def screens(client: JiraClient) -> list[dict[str, object]]:
    """Extract screens.

    Returns list of normalized records.
    Raises JiraError on validation or HTTP errors.
    """
    endpoint = "/rest/api/2/screens"
    by_id: dict[str, dict[str, object]] = {}

    try:
        start = 0
        while True:
            response = await client.call("getAllScreens", startAt=start)
            payload = response.json()

            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("values"), list)
                or not isinstance(payload.get("total"), int)
            ):
                raise ValueError("Invalid screens page")

            values = payload["values"]
            for screen in values:
                if not isinstance(screen, dict) or not isinstance(
                    screen.get("id"), int
                ):
                    raise ValueError("Invalid screen identifier")

                identifier = str(screen["id"])
                name = screen.get("name")
                by_id[identifier] = _record(
                    "screen",
                    identifier,
                    endpoint,
                    screen={
                        "name": (
                            "[REDACTED]"
                            if isinstance(name, str) and SECRET_ASSIGNMENT.search(name)
                            else name
                        )
                    },
                )

            start += len(values)
            if start >= payload["total"]:
                break
            if not values:
                raise ValueError("Empty screens page before total")

    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise JiraError(
            "Network error",
            error_type="RequestError",
            stage_status="failed",
        ) from exc
    except ValueError as exc:
        raise JiraError(
            str(exc),
            error_type="ValueError",
            stage_status="failed",
        ) from exc

    return [by_id[key] for key in sorted(by_id, key=int)]


async def screen_tabs(
    client: JiraClient, screen_ids: list[str]
) -> list[dict[str, object]]:
    """Extract screen tabs for given screen IDs.

    Returns list of normalized records.
    Raises JiraError on validation or HTTP errors.
    """
    by_id: dict[str, dict[str, object]] = {}

    try:
        for screen_id in screen_ids:
            endpoint = f"/rest/api/2/screens/{screen_id}/tabs"
            response = await client.call("getAllTabs", screenId=screen_id)
            tabs = response.json()

            if not isinstance(tabs, list):
                raise ValueError("Invalid screen tabs response")

            for position, tab in enumerate(tabs):
                if not isinstance(tab, dict) or not isinstance(tab.get("id"), int):
                    raise ValueError("Invalid screen tab identifier")

                tab_id = str(tab["id"])
                name = tab.get("name")
                by_id[f"{screen_id}:{tab_id}"] = _record(
                    "screen_tab",
                    f"{screen_id}:{tab_id}",
                    endpoint,
                    screen_id=screen_id,
                    position=position,
                    name=(
                        "[REDACTED]"
                        if isinstance(name, str) and SECRET_ASSIGNMENT.search(name)
                        else name
                    ),
                )

    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise JiraError(
            "Network error",
            error_type="RequestError",
            stage_status="failed",
        ) from exc
    except ValueError as exc:
        raise JiraError(
            str(exc),
            error_type="ValueError",
            stage_status="failed",
        ) from exc

    return [by_id[key] for key in sorted(by_id)]


async def screen_placements(
    client: JiraClient, tabs: list[tuple[str, str]]
) -> list[dict[str, object]]:
    """Extract screen field placements for given (screen_id, tab_id) tuples.

    Args:
        tabs: List of (screen_id, tab_id) tuples.

    Returns list of normalized records sorted by (screen_id, tab_id, position).
    Raises JiraError on validation or HTTP errors.
    """
    by_id: dict[str, dict[str, object]] = {}

    try:
        for screen_id, tab_id in tabs:
            endpoint = f"/rest/api/2/screens/{screen_id}/tabs/{tab_id}/fields"
            response = await client.call(
                "getAllFields", screenId=screen_id, tabId=tab_id
            )
            fields = response.json()

            if not isinstance(fields, list):
                raise ValueError("Invalid screen fields response")

            for position, field in enumerate(fields):
                if (
                    not isinstance(field, dict)
                    or not isinstance(field.get("id"), str)
                    or not field["id"]
                ):
                    raise ValueError("Invalid screen field identifier")

                field_id = field["id"]
                identifier = f"{screen_id}:{tab_id}:{field_id}"
                by_id[identifier] = _record(
                    "screen_placement",
                    identifier,
                    endpoint,
                    screen_id=screen_id,
                    tab_id=tab_id,
                    field_id=field_id,
                    position=position,
                    show_when_empty=field.get("showWhenEmpty"),
                )

    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise JiraError(
            "Network error",
            error_type="RequestError",
            stage_status="failed",
        ) from exc
    except ValueError as exc:
        raise JiraError(
            str(exc),
            error_type="ValueError",
            stage_status="failed",
        ) from exc

    return [
        by_id[key]
        for key in sorted(
            by_id,
            key=lambda key: (
                int(key.split(":", 1)[0]),
                int(key.split(":", 2)[1]),
                by_id[key]["position"],
            ),
        )
    ]
