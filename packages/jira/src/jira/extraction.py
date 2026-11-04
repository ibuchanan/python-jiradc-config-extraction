"""Jira API extraction: fields, custom fields, options, projects, schemes, screens."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from .errors import JiraError, describe_exception
from .status import StageStatus

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator
    from typing import Any

    from .client import JiraClient

logger = logging.getLogger(__name__)

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


@dataclass(frozen=True)
class ProjectRef:
    """A project's source ID and key, as used by project-scoped stages."""

    project_id: str
    key: str


@dataclass(frozen=True)
class ScreenTab:
    """A tab within a screen: one unit of work for tab-field extraction."""

    screen_id: str
    tab_id: str


@dataclass(frozen=True)
class IssueTypeScreenTarget:
    """A project and one of its issue types, whose view screen is extracted."""

    project: ProjectRef
    issue_type_id: str


@dataclass(frozen=True)
class FieldEvidence:
    """A field's source ID, endpoint, and complete response."""

    field_id: str
    endpoint: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class AppEvidence:
    """An installed app's source ID, endpoint, view, and complete response."""

    key: str
    endpoint: str
    name: str | None
    version: str | None
    raw: dict[str, Any]


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


def _keep_first[T](by_id: dict[str, T], identifier: str, value: T) -> None:
    """Record a value under its source ID, keeping the first occurrence.

    Jira can return the same source ID more than once in a page or unit. The
    duplicate-source-ID policy is first occurrence wins: later values with the
    same ID are ignored. The CLI's stage file applies the same policy across
    units and resumed runs, so one policy holds for a whole stage.
    """
    by_id.setdefault(identifier, value)


def _invalid_response(message: str, cause: BaseException | None = None) -> JiraError:
    """Report a malformed Jira response; these messages are static and safe to show."""
    logger.debug("Invalid Jira response: %s", message, exc_info=cause)
    return JiraError(
        message,
        error_type="ValueError",
        stage_status=StageStatus.FAILED,
        detail=message,
    )


def _map_request_error(exc: httpx.RequestError) -> JiraError:
    """Map a transport failure (DNS, TLS, connect, timeout) to a diagnosable error."""
    detail = describe_exception(exc)
    logger.debug("Jira request failed: %s", detail, exc_info=exc)
    return JiraError(
        "Network error",
        error_type="RequestError",
        stage_status=StageStatus.FAILED,
        detail=detail,
    )


def _map_http_error(exc: httpx.HTTPStatusError) -> JiraError:
    """Map HTTP status to JiraError with appropriate stage_status."""
    status_code = exc.response.status_code
    if status_code in _UNAUTHORIZED_CODES:
        stage_status = StageStatus.UNAUTHORIZED
    elif status_code in _UNSUPPORTED_CODES:
        stage_status = StageStatus.UNSUPPORTED
    else:
        stage_status = StageStatus.FAILED
    return JiraError(
        f"HTTP {status_code}",
        status_code=status_code,
        error_type="HTTPStatusError",
        stage_status=stage_status,
    )


@asynccontextmanager
async def _jira_errors() -> AsyncGenerator[None]:
    """Map transport, HTTP, and response-validation failures to safe JiraErrors.

    Every extraction function wraps its Jira access in this context manager so
    the error policy lives in one place. Validation code raises ``ValueError``
    with a static message; that becomes a failed stage without exposing the
    response body.
    """
    try:
        yield
    except httpx.HTTPStatusError as exc:
        raise _map_http_error(exc) from exc
    except httpx.RequestError as exc:
        raise _map_request_error(exc) from exc
    except ValueError as exc:
        raise _invalid_response(str(exc), exc) from exc


def _require_dict(value: object, message: str) -> dict[str, Any]:
    """Return a Jira object response or fail with a safe validation message."""
    if not isinstance(value, dict):
        raise ValueError(message)
    return value


def _require_list(value: object, message: str) -> list[Any]:
    """Return a Jira list response or fail with a safe validation message."""
    if not isinstance(value, list):
        raise ValueError(message)
    return value


def _paged(payload: object, key: str, message: str) -> tuple[list[Any], int]:
    """Return the items and total of a paged Jira response."""
    page = _require_dict(payload, message)
    items = _require_list(page.get(key), message)
    total = page.get("total")
    if not isinstance(total, int):
        raise ValueError(message)
    return items, total


async def field_inventory(client: JiraClient) -> list[dict[str, object]]:
    """Extract custom field inventory.

    Returns list of dicts with schema_version, source, extracted_at, and field data.
    Raises JiraError on validation or HTTP errors.
    """
    endpoint = "/rest/api/2/field"
    async with _jira_errors():
        response = await client.call("getFields")
        fields = _require_list(response.json(), "Jira field inventory was not a list")

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

        by_id: dict[str, Any] = {}
        for field in custom_field_defs:
            _keep_first(by_id, field["id"], field)
        custom_field_defs = [by_id[field_id] for field_id in sorted(by_id)]

        return [
            _record(
                "field",
                field["id"],
                endpoint,
                raw=field,
                field={
                    "name": field.get("name"),
                    **(
                        {"schema": {"custom": field["schema"]["custom"]}}
                        if isinstance(field.get("schema"), dict)
                        and isinstance(field["schema"].get("custom"), str)
                        else {}
                    ),
                },
            )
            for field in custom_field_defs
        ]


async def server_version(client: JiraClient) -> str | None:
    """Extract Jira server version.

    Returns version string or None when the endpoint is unsupported.
    Raises JiraError on other HTTP errors.
    """
    try:
        async with _jira_errors():
            response = await client.call("getServerInfo")
            info = response.json()
    except JiraError as exc:
        if exc.stage_status is StageStatus.UNSUPPORTED:
            return None
        raise

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

    async with _jira_errors():
        start = start_at
        while True:
            response = await client.call("getCustomFields", startAt=start)
            values, total = _paged(
                response.json(), "values", "Invalid custom fields page"
            )

            by_id: dict[str, dict[str, object]] = {}
            for field in values:
                if not isinstance(field, dict) or not isinstance(field.get("id"), str):
                    raise ValueError("Invalid custom field identifier")
                if field["id"] in field_id_set:
                    _keep_first(
                        by_id,
                        field["id"],
                        _record(
                            "custom_fields",
                            field["id"],
                            endpoint,
                            raw=field,
                            field_id=field["id"],
                            project_ids=sorted(
                                str(value) for value in field.get("projectIds", [])
                            ),
                            issue_type_ids=sorted(
                                str(value) for value in field.get("issueTypeIds", [])
                            ),
                            all_projects=field.get("isAllProjects"),
                        ),
                    )

            start += len(values)
            yield Batch([by_id[key] for key in sorted(by_id)], start)
            if start >= total:
                break
            if not values:
                raise ValueError("Empty custom fields page before total")


async def custom_field_options(
    client: JiraClient, field_ids: list[str], start_at: int = 0
) -> AsyncIterator[Batch]:
    """Yield option records one custom field at a time.

    ``start_at`` is the index into ``field_ids`` to resume from. Raises JiraError
    on validation or HTTP errors.
    """
    async with _jira_errors():
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
                options, total = _paged(
                    response.json(),
                    "options",
                    "Invalid custom field options page",
                )

                for option in options:
                    if not isinstance(option, dict) or not isinstance(
                        option.get("id"), int
                    ):
                        raise ValueError("Invalid custom field option identifier")

                    option_id = str(option["id"])
                    value = option.get("value")
                    _keep_first(
                        by_id,
                        option_id,
                        _record(
                            "custom_field_option",
                            f"{field_id}:{option_id}",
                            endpoint,
                            raw=option,
                            field_id=field_id,
                            value=value,
                            disabled=option.get("disabled"),
                            child_ids=[
                                str(child) for child in option.get("childrenIds", [])
                            ],
                        ),
                    )

                seen += len(options)
                if seen >= total:
                    break
                if not options:
                    raise ValueError("Empty options page before total")
                page += 1

            yield Batch([by_id[key] for key in sorted(by_id)], index + 1)


async def screens(client: JiraClient, start_at: int = 0) -> AsyncIterator[Batch]:
    """Yield screen records one page at a time.

    ``start_at`` is the Jira ``startAt`` offset to resume from. Raises JiraError
    on validation or HTTP errors.
    """
    endpoint = "/rest/api/2/screens"

    async with _jira_errors():
        start = start_at
        while True:
            response = await client.call(
                "getAllScreens", startAt=start, maxResults=SCREENS_PAGE_SIZE
            )
            values, total = _paged(response.json(), "screens", "Invalid screens page")

            by_id: dict[str, dict[str, object]] = {}
            for screen in values:
                if not isinstance(screen, dict) or not isinstance(
                    screen.get("id"), int
                ):
                    raise ValueError("Invalid screen identifier")

                identifier = str(screen["id"])
                name = screen.get("name")
                _keep_first(
                    by_id,
                    identifier,
                    _record(
                        "screen",
                        identifier,
                        endpoint,
                        raw=screen,
                        screen={"name": name},
                    ),
                )

            start += len(values)
            yield Batch([by_id[key] for key in sorted(by_id, key=int)], start)
            if start >= total:
                break
            if not values:
                raise ValueError("Empty screens page before total")


async def screen_tabs(
    client: JiraClient, screen_ids: list[str], start_at: int = 0
) -> AsyncIterator[Batch]:
    """Yield tab records one screen at a time.

    ``start_at`` is the index into ``screen_ids`` to resume from. Raises JiraError
    on validation or HTTP errors.
    """
    async with _jira_errors():
        for index in range(start_at, len(screen_ids)):
            screen_id = screen_ids[index]
            endpoint = f"/rest/api/2/screens/{screen_id}/tabs"
            response = await client.call("getAllTabs", screenId=screen_id)
            tabs = _require_list(response.json(), "Invalid screen tabs response")

            by_id: dict[str, dict[str, object]] = {}
            for position, tab in enumerate(tabs):
                if not isinstance(tab, dict) or not isinstance(tab.get("id"), int):
                    raise ValueError("Invalid screen tab identifier")

                tab_id = str(tab["id"])
                name = tab.get("name")
                _keep_first(
                    by_id,
                    tab_id,
                    _record(
                        "screen_tab",
                        f"{screen_id}:{tab_id}",
                        endpoint,
                        raw=tab,
                        screen_id=screen_id,
                        position=position,
                        name=name,
                    ),
                )

            yield Batch(list(by_id.values()), index + 1)


async def screen_tab_fields(
    client: JiraClient, tabs: list[ScreenTab], start_at: int = 0
) -> AsyncIterator[Batch]:
    """Yield the fields on each tab, one tab at a time.

    ``start_at`` is the index into ``tabs`` to resume from. Records within a tab
    follow Jira's response order (``position``). Raises JiraError on validation or
    HTTP errors.
    """
    async with _jira_errors():
        for index in range(start_at, len(tabs)):
            tab = tabs[index]
            screen_id, tab_id = tab.screen_id, tab.tab_id
            endpoint = f"/rest/api/2/screens/{screen_id}/tabs/{tab_id}/fields"
            response = await client.call(
                "getAllFields", screenId=screen_id, tabId=tab_id
            )
            fields = _require_list(response.json(), "Invalid screen fields response")

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
                _keep_first(
                    by_id,
                    identifier,
                    _record(
                        "screen_tab_field",
                        identifier,
                        endpoint,
                        raw=field,
                        screen_id=screen_id,
                        tab_id=tab_id,
                        field_id=field_id,
                        position=position,
                        show_when_empty=field.get("showWhenEmpty"),
                    ),
                )

            yield Batch(list(by_id.values()), index + 1)


def _identifier(value: object) -> str | None:
    """Return a non-empty string or integer identifier as a string."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return str(value) or None


def natural_sort_key(identifier: str) -> tuple[int, str]:
    """Sort canonical integer ID strings by value: shorter first, then text."""
    return (len(identifier), identifier)


async def installed_apps(client: JiraClient, start_at: int = 0) -> AsyncIterator[Batch]:
    """Yield detected user-installed apps and their versions (one batch).

    The UPM REST module at ``/rest/plugins/1.0/`` is not part of the documented
    Jira REST API, so this inventory is optional evidence, not configuration.
    Only user-installed plugins with a stable key are recorded. ``start_at`` of 1
    means the batch was already committed. Raises JiraError on validation or HTTP
    errors.
    """
    if start_at >= 1:
        return
    endpoint = "/rest/plugins/1.0/"
    async with _jira_errors():
        response = await client.call("getInstalledPlugins")
        payload = response.json()
        plugins = (
            _require_list(payload.get("plugins"), "Invalid plugins response")
            if isinstance(payload, dict)
            else _require_list(payload, "Invalid plugins response")
        )

        by_id: dict[str, dict[str, object]] = {}
        for plugin in plugins:
            if not isinstance(plugin, dict) or plugin.get("userInstalled") is not True:
                continue
            identifier = _identifier(plugin.get("key"))
            if identifier is None:
                continue
            version = plugin.get("version")
            vendor = plugin.get("vendor")
            _keep_first(
                by_id,
                identifier,
                _record(
                    "app",
                    identifier,
                    endpoint,
                    raw=plugin,
                    app={
                        "key": identifier,
                        "name": plugin.get("name"),
                        "version": (
                            version if isinstance(version, str) and version else None
                        ),
                        "vendor": (
                            vendor.get("name") if isinstance(vendor, dict) else None
                        ),
                        "enabled": plugin.get("enabled"),
                        "user_installed": True,
                    },
                ),
            )

        yield Batch([by_id[key] for key in sorted(by_id)], 1)


def field_app_associations(
    fields: list[FieldEvidence], apps: list[AppEvidence]
) -> list[dict[str, Any]]:
    """Link custom fields to detected apps by exact field-type-key prefix.

    A custom field's ``schema.custom`` value is ``<plugin key>:<type>``. When the
    prefix exactly equals a detected user-installed app key, the complete field
    and app responses are kept together as evidence. No association is inferred
    from a field name, and no app configuration is claimed.
    """
    apps_by_key = {app.key: app for app in apps}
    records: list[dict[str, Any]] = []
    for field in fields:
        schema = field.raw.get("schema")
        if not isinstance(schema, dict):
            continue
        custom = schema.get("custom")
        if not isinstance(custom, str) or ":" not in custom:
            continue

        app_key = custom.split(":", 1)[0]
        app = apps_by_key.get(app_key)
        if app is None:
            continue

        records.append(
            _record(
                "field_app_association",
                f"{field.field_id}:{app_key}",
                field.endpoint,
                raw={"field": field.raw, "app": app.raw},
                field_id=field.field_id,
                app_key=app_key,
                app={"name": app.name, "version": app.version},
                evidence={
                    "field_type_key": custom,
                    "app_key": app_key,
                    "app_endpoint": app.endpoint,
                    "match": "field schema.custom prefix equals installed app key",
                },
            )
        )

    return sorted(records, key=lambda record: str(record["source"]["id"]))


async def projects(client: JiraClient, start_at: int = 0) -> AsyncIterator[Batch]:
    """Yield the project inventory (one request, so a single batch).

    Archived projects are included and flagged. ``start_at`` of 1 means the batch
    was already committed. Raises JiraError on validation or HTTP errors.
    """
    if start_at >= 1:
        return
    endpoint = "/rest/api/2/project"
    async with _jira_errors():
        response = await client.call("getAllProjects", includeArchived=True)
        payload = _require_list(response.json(), "Invalid projects response")

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
            _keep_first(
                by_id,
                identifier,
                _record(
                    "project",
                    identifier,
                    endpoint,
                    raw=project,
                    project={
                        "key": project["key"],
                        "name": project.get("name"),
                        "project_type_key": project.get("projectTypeKey"),
                        "archived": project.get("archived"),
                    },
                ),
            )

        yield Batch([by_id[key] for key in sorted(by_id, key=natural_sort_key)], 1)


async def issue_types(client: JiraClient, start_at: int = 0) -> AsyncIterator[Batch]:
    """Yield the issue type inventory (one request, so a single batch).

    ``start_at`` of 1 means the batch was already committed. Raises JiraError on
    validation or HTTP errors.
    """
    if start_at >= 1:
        return
    endpoint = "/rest/api/2/issuetype"
    async with _jira_errors():
        response = await client.call("getIssueAllTypes")
        payload = _require_list(response.json(), "Invalid issue types response")

        by_id: dict[str, dict[str, object]] = {}
        for issue_type in payload:
            identifier = (
                _identifier(issue_type.get("id"))
                if isinstance(issue_type, dict)
                else None
            )
            if identifier is None:
                raise ValueError("Invalid issue type identifier")
            _keep_first(
                by_id,
                identifier,
                _record(
                    "issue_type",
                    identifier,
                    endpoint,
                    raw=issue_type,
                    issue_type={
                        "name": issue_type.get("name"),
                        "subtask": issue_type.get("subtask"),
                    },
                ),
            )

        yield Batch([by_id[key] for key in sorted(by_id, key=natural_sort_key)], 1)


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
    async with _jira_errors():
        response = await client.call("getAllIssueTypeSchemes")
        listing = _require_dict(response.json(), "Invalid issue type schemes response")
        schemes = _require_list(
            listing.get("schemes"), "Invalid issue type schemes response"
        )
        scheme_ids = sorted(
            {
                identifier
                for scheme in schemes
                if isinstance(scheme, dict)
                and (identifier := _identifier(scheme.get("id"))) is not None
            },
            key=natural_sort_key,
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
                        raw={"scheme": scheme, "associations": associations},
                        issue_type_scheme={
                            "name": scheme.get("name"),
                            "issue_type_ids": sorted(
                                {str(i) for i in issue_type_ids}, key=natural_sort_key
                            ),
                            "default_issue_type_id": (
                                _identifier(default.get("id"))
                                if isinstance(default, dict)
                                else None
                            ),
                            "project_ids": sorted(
                                {str(i) for i in project_ids}, key=natural_sort_key
                            ),
                        },
                    )
                ],
                index + 1,
            )


async def project_workflow_schemes(
    client: JiraClient, projects: list[ProjectRef], start_at: int = 0
) -> AsyncIterator[Batch]:
    """Yield the workflow scheme assigned to each project.

    ``start_at`` is the index into ``projects`` to resume from. The record is keyed
    by project ID because a workflow scheme can be shared by many projects.
    Raises JiraError on validation or HTTP errors.
    """
    async with _jira_errors():
        for index in range(start_at, len(projects)):
            project = projects[index]
            project_id, project_key = project.project_id, project.key
            endpoint = f"/rest/api/2/project/{project_key}/workflowscheme"
            response = await client.call(
                "getWorkflowSchemeForProject", projectKeyOrId=project_key
            )
            scheme = _require_dict(response.json(), "Invalid workflow scheme")

            if _identifier(scheme.get("id")) is None:
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
                        raw=scheme,
                        project_id=project_id,
                        workflow_scheme_id=_identifier(scheme.get("id")),
                        workflow_scheme_name=scheme.get("name"),
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


async def project_issue_type_view_screens(
    client: JiraClient, targets: list[IssueTypeScreenTarget], start_at: int = 0
) -> AsyncIterator[Batch]:
    """Yield the view screen of each project and issue type target.

    The screen is the one the project uses for viewing an issue of that type. This
    reads Jira's internal project-settings API (``/rest/projectconfig/1``), which is
    not part of the documented REST API and may change between versions; only the
    View operation is exposed, not Create or Edit. ``start_at`` is the index into
    ``targets`` to resume from. Raises JiraError on validation or HTTP errors.
    """
    async with _jira_errors():
        for index in range(start_at, len(targets)):
            target = targets[index]
            project_id, project_key = target.project.project_id, target.project.key
            issue_type_id = target.issue_type_id
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
                        raw=payload,
                        project_id=project_id,
                        issue_type_id=issue_type_id,
                        view_screen={
                            "screen_id": screen_id,
                            "screen_name": screen.get("screenName"),
                            "state": screen.get("state"),
                            "shared_with_project_ids": sorted(
                                {str(i) for i in project_ids}, key=natural_sort_key
                            ),
                            "shared_with_issue_type_ids": sorted(
                                {str(i) for i in issue_type_ids}, key=natural_sort_key
                            ),
                            "total_projects_count": screen.get("totalProjectsCount"),
                            "hidden_projects_count": screen.get("hiddenProjectsCount"),
                        },
                    )
                ],
                index + 1,
            )
