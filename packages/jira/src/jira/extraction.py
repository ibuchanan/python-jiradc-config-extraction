"""Jira API extraction: fields, custom fields, options, projects, schemes, screens."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from .errors import JiraError, describe_exception

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from .client import JiraClient

logger = logging.getLogger(__name__)

SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:password|passwd|token|secret|api[_-]?key|credential|authorization)\b\s*[:=]"
)

_UNAUTHORIZED_CODES = (401, 403)
_UNSUPPORTED_CODES = (404, 405, 501)
# Jira DC rejects /screens unless startAt and maxResults are sent together.
SCREENS_PAGE_SIZE = 100


@dataclass(frozen=True)
class Batch:
    """Records from one resumable unit of work and where to resume after it.

    ``cursor`` is the value to pass as ``start_at`` to continue after this batch:
    a Jira ``startAt`` offset for paged endpoints, or the index of the next item
    for per-field, per-screen, and per-tab stages.
    """

    records: list[dict[str, object]]
    cursor: int


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


def _invalid_response(message: str, cause: BaseException | None = None) -> JiraError:
    """Report a malformed Jira response; these messages are static and safe to show."""
    logger.debug("Invalid Jira response: %s", message, exc_info=cause)
    return JiraError(
        message, error_type="ValueError", stage_status="failed", detail=message
    )


def _map_request_error(exc: httpx.RequestError) -> JiraError:
    """Map a transport failure (DNS, TLS, connect, timeout) to a diagnosable error."""
    detail = describe_exception(exc)
    logger.debug("Jira request failed: %s", detail, exc_info=exc)
    return JiraError(
        "Network error",
        error_type="RequestError",
        stage_status="failed",
        detail=detail,
    )


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
        raise _map_request_error(exc) from exc

    if not isinstance(fields, list):
        raise _invalid_response("Jira field inventory was not a list")

    custom_field_defs = [
        field
        for field in fields
        if isinstance(field, dict) and field.get("custom") is True
    ]

    if any(
        not isinstance(field.get("id"), str) or not field["id"]
        for field in custom_field_defs
    ):
        raise _invalid_response("Jira custom field missing a stable id")

    by_id = {field["id"]: field for field in custom_field_defs}
    custom_field_defs = [by_id[field_id] for field_id in sorted(by_id)]

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
        for field in custom_field_defs
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
        raise _map_request_error(exc) from exc

    if not isinstance(info, dict):
        return None

    version = info.get("version")
    return version if isinstance(version, str) else None


async def custom_fields(
    client: JiraClient, field_ids: list[str], start_at: int = 0
) -> AsyncIterator[Batch]:
    """Yield custom fields (with project/issue type scope) one page at a time.

    ``start_at`` is the Jira ``startAt`` offset to resume from (a prior batch's
    ``cursor``). Raises JiraError on validation or HTTP errors.
    """
    endpoint = "/rest/api/2/customFields"
    field_id_set = set(field_ids)

    try:
        start = start_at
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
            by_id: dict[str, dict[str, object]] = {}
            for field in values:
                if not isinstance(field, dict) or not isinstance(field.get("id"), str):
                    raise ValueError("Invalid custom field identifier")
                if field["id"] in field_id_set:
                    by_id[field["id"]] = _record(
                        "custom_fields",
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
            yield Batch([by_id[key] for key in sorted(by_id)], start)
            if start >= payload["total"]:
                break
            if not values:
                raise ValueError("Empty custom fields page before total")

    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise _map_request_error(exc) from exc
    except ValueError as exc:
        raise _invalid_response(str(exc), exc) from exc


async def custom_field_options(
    client: JiraClient, field_ids: list[str], start_at: int = 0
) -> AsyncIterator[Batch]:
    """Yield option records one custom field at a time.

    ``start_at`` is the index into ``field_ids`` to resume from. Raises JiraError
    on validation or HTTP errors.
    """
    try:
        for index in range(start_at, len(field_ids)):
            field_id = field_ids[index]
            if (
                not field_id.startswith("customfield_")
                or not field_id.removeprefix("customfield_").isdigit()
            ):
                raise ValueError("Invalid custom field id")

            num = field_id.removeprefix("customfield_")
            endpoint = f"/rest/api/2/customFields/{num}/options"
            by_id: dict[str, dict[str, object]] = {}
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
                    by_id[option_id] = _record(
                        "custom_field_option",
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

            yield Batch([by_id[key] for key in sorted(by_id)], index + 1)

    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise _map_request_error(exc) from exc
    except ValueError as exc:
        raise _invalid_response(str(exc), exc) from exc


async def screens(client: JiraClient, start_at: int = 0) -> AsyncIterator[Batch]:
    """Yield screen records one page at a time.

    ``start_at`` is the Jira ``startAt`` offset to resume from. Raises JiraError
    on validation or HTTP errors.
    """
    endpoint = "/rest/api/2/screens"

    try:
        start = start_at
        while True:
            response = await client.call(
                "getAllScreens", startAt=start, maxResults=SCREENS_PAGE_SIZE
            )
            payload = response.json()

            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("screens"), list)
                or not isinstance(payload.get("total"), int)
            ):
                raise ValueError("Invalid screens page")

            values = payload["screens"]
            by_id: dict[str, dict[str, object]] = {}
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
            yield Batch([by_id[key] for key in sorted(by_id, key=int)], start)
            if start >= payload["total"]:
                break
            if not values:
                raise ValueError("Empty screens page before total")

    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise _map_request_error(exc) from exc
    except ValueError as exc:
        raise _invalid_response(str(exc), exc) from exc


async def screen_tabs(
    client: JiraClient, screen_ids: list[str], start_at: int = 0
) -> AsyncIterator[Batch]:
    """Yield tab records one screen at a time.

    ``start_at`` is the index into ``screen_ids`` to resume from. Raises JiraError
    on validation or HTTP errors.
    """
    try:
        for index in range(start_at, len(screen_ids)):
            screen_id = screen_ids[index]
            endpoint = f"/rest/api/2/screens/{screen_id}/tabs"
            response = await client.call("getAllTabs", screenId=screen_id)
            tabs = response.json()

            if not isinstance(tabs, list):
                raise ValueError("Invalid screen tabs response")

            by_id: dict[str, dict[str, object]] = {}
            for position, tab in enumerate(tabs):
                if not isinstance(tab, dict) or not isinstance(tab.get("id"), int):
                    raise ValueError("Invalid screen tab identifier")

                tab_id = str(tab["id"])
                name = tab.get("name")
                by_id[tab_id] = _record(
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

            yield Batch(list(by_id.values()), index + 1)

    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise _map_request_error(exc) from exc
    except ValueError as exc:
        raise _invalid_response(str(exc), exc) from exc


async def screen_tab_fields(
    client: JiraClient, tabs: list[tuple[str, str]], start_at: int = 0
) -> AsyncIterator[Batch]:
    """Yield the fields on each (screen_id, tab_id) tab, one tab at a time.

    ``start_at`` is the index into ``tabs`` to resume from. Records within a tab
    follow Jira's response order (``position``). Raises JiraError on validation or
    HTTP errors.
    """
    try:
        for index in range(start_at, len(tabs)):
            screen_id, tab_id = tabs[index]
            endpoint = f"/rest/api/2/screens/{screen_id}/tabs/{tab_id}/fields"
            response = await client.call(
                "getAllFields", screenId=screen_id, tabId=tab_id
            )
            fields = response.json()

            if not isinstance(fields, list):
                raise ValueError("Invalid screen fields response")

            by_id: dict[str, dict[str, object]] = {}
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
                    "screen_tab_field",
                    identifier,
                    endpoint,
                    screen_id=screen_id,
                    tab_id=tab_id,
                    field_id=field_id,
                    position=position,
                    show_when_empty=field.get("showWhenEmpty"),
                )

            yield Batch(list(by_id.values()), index + 1)

    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise _map_request_error(exc) from exc
    except ValueError as exc:
        raise _invalid_response(str(exc), exc) from exc


def _safe_name(value: object) -> object:
    """Redact names that look like credential assignments, as other stages do."""
    if isinstance(value, str) and SECRET_ASSIGNMENT.search(value):
        return "[REDACTED]"
    return value


def _identifier(value: object) -> str | None:
    """Return a non-empty string or integer identifier as a string."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return str(value) or None


def _numeric_order(identifier: str) -> tuple[int, str]:
    return (len(identifier), identifier)


async def projects(client: JiraClient, start_at: int = 0) -> AsyncIterator[Batch]:
    """Yield the project inventory (one request, so a single batch).

    Archived projects are included and flagged. ``start_at`` of 1 means the batch
    was already committed. Raises JiraError on validation or HTTP errors.
    """
    if start_at >= 1:
        return
    endpoint = "/rest/api/2/project"
    try:
        response = await client.call("getAllProjects", includeArchived=True)
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Invalid projects response")

        by_id: dict[str, dict[str, object]] = {}
        for project in payload:
            identifier = (
                _identifier(project.get("id")) if isinstance(project, dict) else None
            )
            if (
                identifier is None
                or not isinstance(project.get("key"), str)
                or not project["key"]
            ):
                raise ValueError("Invalid project identifier")
            by_id[identifier] = _record(
                "project",
                identifier,
                endpoint,
                project={
                    "key": project["key"],
                    "name": _safe_name(project.get("name")),
                    "project_type_key": project.get("projectTypeKey"),
                    "archived": project.get("archived"),
                },
            )

        yield Batch([by_id[key] for key in sorted(by_id, key=_numeric_order)], 1)

    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise _map_request_error(exc) from exc
    except ValueError as exc:
        raise _invalid_response(str(exc), exc) from exc


async def issue_types(client: JiraClient, start_at: int = 0) -> AsyncIterator[Batch]:
    """Yield the issue type inventory (one request, so a single batch).

    ``start_at`` of 1 means the batch was already committed. Raises JiraError on
    validation or HTTP errors.
    """
    if start_at >= 1:
        return
    endpoint = "/rest/api/2/issuetype"
    try:
        response = await client.call("getIssueAllTypes")
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Invalid issue types response")

        by_id: dict[str, dict[str, object]] = {}
        for issue_type in payload:
            identifier = (
                _identifier(issue_type.get("id"))
                if isinstance(issue_type, dict)
                else None
            )
            if identifier is None:
                raise ValueError("Invalid issue type identifier")
            by_id[identifier] = _record(
                "issue_type",
                identifier,
                endpoint,
                issue_type={
                    "name": _safe_name(issue_type.get("name")),
                    "subtask": issue_type.get("subtask"),
                },
            )

        yield Batch([by_id[key] for key in sorted(by_id, key=_numeric_order)], 1)

    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise _map_request_error(exc) from exc
    except ValueError as exc:
        raise _invalid_response(str(exc), exc) from exc


async def issue_type_schemes(
    client: JiraClient, start_at: int = 0
) -> AsyncIterator[Batch]:
    """Yield one record per issue type scheme: its issue types and its projects.

    The scheme list is fetched on every run and ordered by numeric ID, so
    ``start_at`` (an index into it) stays valid unless a scheme is created between
    a failure and the resume. Each scheme costs two requests: the scheme with
    ``expand=issueTypes,defaultIssueType`` and its project associations. Raises
    JiraError on validation or HTTP errors.
    """
    try:
        response = await client.call("getAllIssueTypeSchemes")
        listing = response.json()
        if not isinstance(listing, dict) or not isinstance(
            listing.get("schemes"), list
        ):
            raise ValueError("Invalid issue type schemes response")
        scheme_ids = sorted(
            {
                identifier
                for scheme in listing["schemes"]
                if isinstance(scheme, dict)
                and (identifier := _identifier(scheme.get("id"))) is not None
            },
            key=_numeric_order,
        )

        for index in range(start_at, len(scheme_ids)):
            scheme_id = scheme_ids[index]
            scheme = (
                await client.call(
                    "getIssueTypeScheme",
                    schemeId=scheme_id,
                    expand="issueTypes,defaultIssueType",
                )
            ).json()
            associations = (
                await client.call("getAssociatedProjects", schemeId=scheme_id)
            ).json()

            if (
                not isinstance(scheme, dict)
                or _identifier(scheme.get("id")) != scheme_id
            ):
                raise ValueError("Invalid issue type scheme")
            issue_types_payload = scheme.get("issueTypes", [])
            if not isinstance(issue_types_payload, list):
                raise ValueError("Invalid issue type scheme issue types")
            issue_type_ids = [
                _identifier(item.get("id")) if isinstance(item, dict) else None
                for item in issue_types_payload
            ]
            if None in issue_type_ids:
                raise ValueError("Invalid issue type scheme issue type identifier")
            if not isinstance(associations, list):
                raise ValueError("Invalid issue type scheme associations")
            project_ids = [
                _identifier(item.get("id")) if isinstance(item, dict) else None
                for item in associations
            ]
            if None in project_ids:
                raise ValueError("Invalid issue type scheme project identifier")
            default = scheme.get("defaultIssueType")

            yield Batch(
                [
                    _record(
                        "issue_type_scheme",
                        scheme_id,
                        f"/rest/api/2/issuetypescheme/{scheme_id}",
                        issue_type_scheme={
                            "name": _safe_name(scheme.get("name")),
                            "issue_type_ids": sorted(
                                {str(i) for i in issue_type_ids}, key=_numeric_order
                            ),
                            "default_issue_type_id": (
                                _identifier(default.get("id"))
                                if isinstance(default, dict)
                                else None
                            ),
                            "project_ids": sorted(
                                {str(i) for i in project_ids}, key=_numeric_order
                            ),
                        },
                    )
                ],
                index + 1,
            )

    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise _map_request_error(exc) from exc
    except ValueError as exc:
        raise _invalid_response(str(exc), exc) from exc


async def project_workflow_schemes(
    client: JiraClient, projects: list[tuple[str, str]], start_at: int = 0
) -> AsyncIterator[Batch]:
    """Yield the workflow scheme assigned to each (project_id, project_key).

    ``start_at`` is the index into ``projects`` to resume from. The record is keyed
    by project ID because a workflow scheme can be shared by many projects.
    Raises JiraError on validation or HTTP errors.
    """
    try:
        for index in range(start_at, len(projects)):
            project_id, project_key = projects[index]
            endpoint = f"/rest/api/2/project/{project_key}/workflowscheme"
            response = await client.call(
                "getWorkflowSchemeForProject", projectKeyOrId=project_key
            )
            scheme = response.json()

            if not isinstance(scheme, dict) or _identifier(scheme.get("id")) is None:
                raise ValueError("Invalid workflow scheme")
            mappings = scheme.get("issueTypeMappings", {})
            if not isinstance(mappings, dict) or not all(
                isinstance(workflow, str) for workflow in mappings.values()
            ):
                raise ValueError("Invalid workflow scheme issue type mappings")
            default_workflow = scheme.get("defaultWorkflow")
            if default_workflow is not None and not isinstance(default_workflow, str):
                raise ValueError("Invalid workflow scheme default workflow")

            yield Batch(
                [
                    _record(
                        "project_workflow_scheme",
                        project_id,
                        endpoint,
                        project_id=project_id,
                        workflow_scheme_id=_identifier(scheme.get("id")),
                        workflow_scheme_name=_safe_name(scheme.get("name")),
                        default_workflow=default_workflow,
                        issue_type_mappings={
                            str(issue_type): workflow
                            for issue_type, workflow in sorted(mappings.items())
                        },
                        draft=scheme.get("draft"),
                    )
                ],
                index + 1,
            )

    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise _map_request_error(exc) from exc
    except ValueError as exc:
        raise _invalid_response(str(exc), exc) from exc


async def project_issue_type_view_screens(
    client: JiraClient, targets: list[tuple[str, str, str]], start_at: int = 0
) -> AsyncIterator[Batch]:
    """Yield the view screen of each (project_id, project_key, issue_type_id).

    The screen is the one the project uses for viewing an issue of that type. This
    reads Jira's internal project-settings API (``/rest/projectconfig/1``), which is
    not part of the documented REST API and may change between versions; only the
    View operation is exposed, not Create or Edit. ``start_at`` is the index into
    ``targets`` to resume from. Raises JiraError on validation or HTTP errors.
    """
    try:
        for index in range(start_at, len(targets)):
            project_id, project_key, issue_type_id = targets[index]
            endpoint = (
                f"/rest/projectconfig/1/issuetype/{project_key}/{issue_type_id}/fields"
            )
            response = await client.call(
                "getProjectConfigIssueTypeFields",
                projectKey=project_key,
                issueTypeId=issue_type_id,
            )
            payload = response.json()

            screen = payload.get("viewScreen") if isinstance(payload, dict) else None
            if (
                not isinstance(screen, dict)
                or (screen_id := _identifier(screen.get("screenId"))) is None
            ):
                raise ValueError("Invalid view screen")
            shared_projects = screen.get("sharedWithProjects", [])
            shared_types = screen.get("sharedWithIssueTypes", [])
            if not isinstance(shared_projects, list) or not isinstance(
                shared_types, list
            ):
                raise ValueError("Invalid view screen sharing")
            project_ids = [
                _identifier(item.get("id")) if isinstance(item, dict) else None
                for item in shared_projects
            ]
            issue_type_ids = [_identifier(item) for item in shared_types]
            if None in project_ids or None in issue_type_ids:
                raise ValueError("Invalid view screen sharing identifier")

            yield Batch(
                [
                    _record(
                        "project_issue_type_view_screen",
                        f"{project_id}:{issue_type_id}",
                        endpoint,
                        project_id=project_id,
                        issue_type_id=issue_type_id,
                        view_screen={
                            "screen_id": screen_id,
                            "screen_name": _safe_name(screen.get("screenName")),
                            "state": screen.get("state"),
                            "shared_with_project_ids": sorted(
                                {str(i) for i in project_ids}, key=_numeric_order
                            ),
                            "shared_with_issue_type_ids": sorted(
                                {str(i) for i in issue_type_ids}, key=_numeric_order
                            ),
                            "total_projects_count": screen.get("totalProjectsCount"),
                            "hidden_projects_count": screen.get("hiddenProjectsCount"),
                        },
                    )
                ],
                index + 1,
            )

    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise _map_request_error(exc) from exc
    except ValueError as exc:
        raise _invalid_response(str(exc), exc) from exc
