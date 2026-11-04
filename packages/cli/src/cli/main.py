"""Export Jira Data Center custom fields as JSON Lines."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any
from uuid import uuid4

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.table import Table

from cli.doctor import run_checks
from cli.stage_file import StageFile
from config import load_jira_dc_settings, normalise_url, url_origin
from jira import (
    AppEvidence,
    Batch,
    FieldEvidence,
    IssueTypeScreenTarget,
    JiraClient,
    JiraError,
    ProjectRef,
    ScreenTab,
    StageStatus,
    describe_exception,
    field_app_associations,
    natural_sort_key,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from config import JiraDCSettings

    type ExtractFunc = Callable[[JiraClient, Path, int], AsyncIterator[Batch]]

app = typer.Typer(help="Export Jira Data Center configuration.", add_completion=False)
console = Console()
error_console = Console(stderr=True)


def _atomic_write(path: Path, content: str) -> None:
    """Install a complete file before publishing checkpoint state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _failure_result(
    exc: Exception, *, status: StageStatus | None = None
) -> dict[str, Any]:
    """Build a safe checkpoint/manifest failure record from an exception."""
    if isinstance(exc, JiraError):
        result: dict[str, Any] = {
            "status": status or exc.stage_status,
            "error": exc.error_type,
        }
        if exc.status_code is not None:
            result["http_status"] = exc.status_code
        detail = exc.detail
    else:
        result = {"status": status or StageStatus.FAILED, "error": type(exc).__name__}
        detail = describe_exception(exc)
    if detail and detail != result["error"]:
        result["detail"] = detail
    return result


def _error_text(exc: Exception) -> str:
    """Return markup-escaped failure text that names the safe underlying cause."""
    if isinstance(exc, JiraError):
        return escape(exc.detail or exc.error_type)
    return escape(describe_exception(exc))


def _configure_logging(*, verbose: bool) -> None:
    """Send debug logs (with tracebacks) from this tool's loggers to stderr."""
    if not verbose:
        return
    handler = RichHandler(console=error_console, show_path=False, markup=False)
    for name in ("cli", "jira"):
        tool_logger = logging.getLogger(name)
        tool_logger.setLevel(logging.DEBUG)
        tool_logger.addHandler(handler)


# Jira request pacing for the whole export. Edit this number to go faster or slower;
# requests are spaced evenly (120 means one request every half second).
REQUESTS_PER_MINUTE = 120


@dataclass(frozen=True)
class Stage:
    """One export stage: its output file, prerequisites, and extractor.

    The order of ``STAGES`` is the run order. The output file, prerequisites,
    internal-API marker, resume state, execution, and coverage all derive from
    this single declaration.

    ``extract`` is ``None`` for the fields stage, which replaces one whole file
    atomically instead of committing resumable batches; every other stage yields
    ``Batch`` records for ``StageFile`` to append and checkpoint.

    ``advisory`` stages are supplementary evidence. A missing, unauthorized, or
    unsupported advisory stage is reported in coverage but does not by itself
    make the run exit non-zero.
    """

    name: str
    filename: str
    extract: ExtractFunc | None = None
    prerequisites: tuple[str, ...] = ()
    internal_api: bool = False
    advisory: bool = False


def _announce_wait(seconds: float) -> None:
    """Show a rate-limit wait inline with the progress dots, e.g. ``(wait:30s)``."""
    console.print(
        f" (wait:{math.ceil(seconds)}s) ",
        end="",
        style="yellow",
        markup=False,
        highlight=False,
        soft_wrap=True,
    )


def _new_client(base_url: str, *, user: str, password: str) -> JiraClient:
    return JiraClient(
        base_url,
        user=user,
        password=password,
        requests_per_minute=REQUESTS_PER_MINUTE,
        on_rate_limit=_announce_wait,
    )


def _print_dots(count: int) -> None:
    """Print one dot per record written, without ending the line."""
    console.print("." * count, end="", markup=False, highlight=False, soft_wrap=True)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a stage file into records, in file order."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _source_ids(path: Path) -> list[str]:
    """Read every record's ``source.id`` from a stage file, in file order."""
    return [str(record["source"]["id"]) for record in _read_jsonl(path)]


def _field_ids(output_dir: Path) -> list[str]:
    """Read the custom field IDs written by the fields stage."""
    return _source_ids(output_dir / _STAGES_BY_NAME["fields"].filename)


def _app_versions(output_dir: Path) -> dict[str, str]:
    """Read detected app versions from the app inventory, if it was written."""
    path = output_dir / _STAGES_BY_NAME["app_inventory"].filename
    if not path.exists():
        return {}
    versions: dict[str, str] = {}
    for record in _read_jsonl(path):
        app = record.get("app")
        if not isinstance(app, dict):
            continue
        key = app.get("key")
        version = app.get("version")
        if isinstance(key, str) and key and isinstance(version, str) and version:
            versions[key] = version
    return versions


def _screen_ids(output_dir: Path) -> list[str]:
    """Read the screen IDs written by the screens stage."""
    return _source_ids(output_dir / _STAGES_BY_NAME["screens"].filename)


def _tabs(output_dir: Path) -> list[ScreenTab]:
    """Read the screen and tab IDs written by the tabs stage."""
    return [
        ScreenTab(record["screen_id"], record["source"]["id"].split(":", 1)[1])
        for record in _read_jsonl(output_dir / _STAGES_BY_NAME["screen_tabs"].filename)
    ]


def _custom_fields(
    client: JiraClient, output_dir: Path, start: int
) -> AsyncIterator[Batch]:
    return client.custom_fields(sorted(_field_ids(output_dir)), start)


def _custom_field_options(
    client: JiraClient, output_dir: Path, start: int
) -> AsyncIterator[Batch]:
    return client.custom_field_options(_field_ids(output_dir), start)


def _app_inventory(
    client: JiraClient, _output_dir: Path, start: int
) -> AsyncIterator[Batch]:
    return client.installed_apps(start)


def _field_evidence(records: list[dict[str, Any]]) -> list[FieldEvidence]:
    """Select the field ID, endpoint, and response the association rule reads."""
    evidence: list[FieldEvidence] = []
    for record in records:
        source = record.get("source")
        raw = record.get("raw")
        if not (isinstance(source, dict) and isinstance(raw, dict)):
            continue
        field_id = source.get("id")
        endpoint = source.get("endpoint")
        if isinstance(field_id, str) and isinstance(endpoint, str):
            evidence.append(FieldEvidence(field_id, endpoint, raw))
    return evidence


def _app_evidence(records: list[dict[str, Any]]) -> list[AppEvidence]:
    """Select the app ID, endpoint, view, and response the rule reads."""
    evidence: list[AppEvidence] = []
    for record in records:
        source = record.get("source")
        app = record.get("app")
        raw = record.get("raw")
        if not (
            isinstance(source, dict) and isinstance(app, dict) and isinstance(raw, dict)
        ):
            continue
        key = source.get("id")
        endpoint = source.get("endpoint")
        if not (isinstance(key, str) and isinstance(endpoint, str)):
            continue
        name = app.get("name")
        version = app.get("version")
        evidence.append(
            AppEvidence(
                key,
                endpoint,
                name if isinstance(name, str) else None,
                version if isinstance(version, str) else None,
                raw,
            )
        )
    return evidence


async def _field_app_associations(
    _client: JiraClient, output_dir: Path, start: int
) -> AsyncIterator[Batch]:
    """Link fields to detected apps; one derived batch, no Jira request."""
    if start >= 1:
        return
    field_records = _read_jsonl(output_dir / _STAGES_BY_NAME["fields"].filename)
    app_records = _read_jsonl(output_dir / _STAGES_BY_NAME["app_inventory"].filename)
    yield Batch(
        field_app_associations(
            _field_evidence(field_records), _app_evidence(app_records)
        ),
        1,
    )


def _projects(
    client: JiraClient, _output_dir: Path, start: int
) -> AsyncIterator[Batch]:
    return client.projects(start)


def _issue_types(
    client: JiraClient, _output_dir: Path, start: int
) -> AsyncIterator[Batch]:
    return client.issue_types(start)


def _issue_type_schemes(
    client: JiraClient, _output_dir: Path, start: int
) -> AsyncIterator[Batch]:
    return client.issue_type_schemes(start)


def _project_workflow_schemes(
    client: JiraClient, output_dir: Path, start: int
) -> AsyncIterator[Batch]:
    projects = [
        ProjectRef(record["source"]["id"], record["project"]["key"])
        for record in _read_jsonl(output_dir / _STAGES_BY_NAME["projects"].filename)
    ]
    return client.project_workflow_schemes(projects, start)


def _issue_type_screen_targets(output_dir: Path) -> list[IssueTypeScreenTarget]:
    """Pair each project with the issue types its issue type scheme offers."""
    issue_types_by_project: dict[str, set[str]] = {}
    for record in _read_jsonl(
        output_dir / _STAGES_BY_NAME["issue_type_schemes"].filename
    ):
        scheme = record["issue_type_scheme"]
        for project_id in scheme["project_ids"]:
            issue_types_by_project.setdefault(project_id, set()).update(
                scheme["issue_type_ids"]
            )
    targets: list[IssueTypeScreenTarget] = []
    for record in _read_jsonl(output_dir / _STAGES_BY_NAME["projects"].filename):
        project = ProjectRef(record["source"]["id"], record["project"]["key"])
        if project.project_id not in issue_types_by_project:
            message = "Project is not in any issue type scheme"
            raise JiraError(
                message,
                error_type="ValueError",
                stage_status=StageStatus.FAILED,
                detail=message,
            )
        targets.extend(
            IssueTypeScreenTarget(project, issue_type_id)
            for issue_type_id in sorted(
                issue_types_by_project[project.project_id], key=natural_sort_key
            )
        )
    return targets


def _project_issue_type_view_screens(
    client: JiraClient, output_dir: Path, start: int
) -> AsyncIterator[Batch]:
    return client.project_issue_type_view_screens(
        _issue_type_screen_targets(output_dir), start
    )


def _screens(client: JiraClient, _output_dir: Path, start: int) -> AsyncIterator[Batch]:
    return client.screens(start)


def _screen_tabs(
    client: JiraClient, output_dir: Path, start: int
) -> AsyncIterator[Batch]:
    return client.screen_tabs(_screen_ids(output_dir), start)


def _screen_tab_fields(
    client: JiraClient, output_dir: Path, start: int
) -> AsyncIterator[Batch]:
    return client.screen_tab_fields(_tabs(output_dir), start)


STAGES: tuple[Stage, ...] = (
    Stage("fields", "fields.jsonl"),
    Stage("custom_fields", "custom-fields.jsonl", _custom_fields),
    Stage("custom_field_options", "custom-field-options.jsonl", _custom_field_options),
    Stage(
        "app_inventory",
        "app-inventory.jsonl",
        _app_inventory,
        internal_api=True,
        advisory=True,
    ),
    Stage(
        "associations",
        "associations.jsonl",
        _field_app_associations,
        prerequisites=("app_inventory",),
        internal_api=True,
        advisory=True,
    ),
    Stage("projects", "projects.jsonl", _projects),
    Stage("issue_types", "issue-types.jsonl", _issue_types),
    Stage("issue_type_schemes", "issue-type-schemes.jsonl", _issue_type_schemes),
    Stage(
        "project_workflow_schemes",
        "project-workflow-schemes.jsonl",
        _project_workflow_schemes,
        prerequisites=("projects",),
    ),
    Stage(
        "project_issue_type_view_screens",
        "project-issue-type-view-screens.jsonl",
        _project_issue_type_view_screens,
        prerequisites=("projects", "issue_type_schemes"),
        internal_api=True,
    ),
    Stage("screens", "screens.jsonl", _screens),
    Stage("screen_tabs", "screen-tabs.jsonl", _screen_tabs, prerequisites=("screens",)),
    Stage(
        "screen_tab_fields",
        "screen-tab-fields.jsonl",
        _screen_tab_fields,
        prerequisites=("screen_tabs",),
    ),
)
# Stage names in run order, and the stages that commit resumable batches.
STAGE_ORDER = tuple(stage.name for stage in STAGES)
_RESUMABLE_STAGES = tuple(stage for stage in STAGES if stage.extract is not None)
_STAGES_BY_NAME = {stage.name: stage for stage in STAGES}


@dataclass(frozen=True)
class DeclaredGap:
    """A known source with no supported read-only route.

    A declared gap is reported as ``unsupported`` with a reason and the evidence
    that no supported read route exists. It is advisory and never makes a run
    exit non-zero on its own.
    """

    name: str
    reason: str
    evidence: str


# Sources with no supported read-only route: declared gaps, not queried stages.
_DECLARED_GAPS: tuple[DeclaredGap, ...] = (
    DeclaredGap(
        "field_contexts",
        "no supported read-only Jira DC field context API identified",
        (
            "The bundled Jira DC 11.2.1 OpenAPI document defines the "
            "FieldConfigScheme bean but no read route; "
            "GET /rest/api/2/field/{id}/configschemes returned 404 on Jira DC "
            "11.3.12, and that instance's application.wadl route lists for "
            "/rest/api/2 and /rest/projectconfig/1 contain no context route."
        ),
    ),
    DeclaredGap(
        "field_configurations",
        "no supported read-only Jira DC field configuration API identified",
        (
            "The only published route that exposes per-issue-type field "
            "settings, GET /rest/projectconfig/1/issuetype/{project}/{issueType}"
            "/fields, returns the view screen collected by "
            "project_issue_type_view_screens; the bundled spec has no field "
            "configuration or field configuration scheme read route."
        ),
    ),
    DeclaredGap(
        "screen_schemes",
        "no supported read-only Jira DC screen scheme API identified",
        (
            "Neither the bundled Jira DC 11.2.1 OpenAPI document nor the "
            "application.wadl route lists published by a Jira DC 11.3.12 "
            "instance contain a read route for screen schemes."
        ),
    ),
    DeclaredGap(
        "issue_type_screen_schemes",
        "no supported read-only Jira DC issue type screen scheme API identified",
        (
            "Neither the bundled Jira DC 11.2.1 OpenAPI document nor the "
            "application.wadl route lists published by a Jira DC 11.3.12 "
            "instance contain a read route for issue type screen schemes."
        ),
    ),
    DeclaredGap(
        "elements_connect",
        "no supported read-only Elements Connect DC config API identified",
        (
            "The published Elements Connect REST API (SwaggerHub v2.0) renders "
            "field content; the field configuration guide documents individual "
            "JSON downloads and an admin Import/Export area, not a read-only "
            "configuration REST API. The UPM module used for installed-app "
            "versions lists plugins, not app configuration."
        ),
    ),
    DeclaredGap(
        "dynamic_forms",
        "no supported read-only Dynamic Forms DC REST API identified",
        (
            "Fields Visibility exports JSON through the admin UI and the Java "
            "ExportConfigurationService, not a REST endpoint; no published "
            "OpenAPI configuration-inventory spec was located."
        ),
    ),
    DeclaredGap(
        "scriptrunner",
        "no supported read-only ScriptRunner DC REST API identified",
        (
            "Script Registry exports a ZIP/JSON and inventory CSV (documented "
            "for 8.54+, 9.19+, 10.0+), and Configuration Exporter is a built-in "
            "script; neither is a REST inventory API, and no published OpenAPI "
            "configuration spec was located."
        ),
    ),
)


def _tag_api(stage: str, result: dict[str, Any]) -> dict[str, Any]:
    """Mark results that came from an internal, undocumented API."""
    if _STAGES_BY_NAME[stage].internal_api:
        result["api"] = "internal"
    return result


def _describe_state(output_dir: Path) -> list[str]:
    """Summarize whether this run starts fresh or resumes earlier output."""
    manifest_path = output_dir / "manifest.json"
    checkpoint_path = output_dir / "checkpoint.json"
    if not manifest_path.exists() and not checkpoint_path.exists():
        return ["Starting a fresh export (no previous run found)."]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        stages = checkpoint["stages"]
        progress = checkpoint.get("progress", {})
        run_id = str(manifest["run_id"])[:8]
        started = manifest["created_at"]
        complete = [
            n
            for n in STAGE_ORDER
            if stages.get(n, {}).get("status") == StageStatus.COMPLETE
        ]
        failed = [
            n
            for n in STAGE_ORDER
            if stages.get(n, {}).get("status") == StageStatus.FAILED
        ]
        partial = {
            n: progress[n] for n in STAGE_ORDER if isinstance(progress.get(n), dict)
        }
        jira_version = manifest.get("jira_version")
    except OSError, ValueError, KeyError, TypeError, AttributeError:
        return [
            "Found existing output that could not be read; the export will report why."
        ]
    if len(complete) == len(STAGE_ORDER):
        detail = (
            "only the Jira version will be probed because it was not recorded"
            if jira_version is None
            else "no records will be fetched"
        )
        return [
            f"Resuming run {run_id} (started {started}): every stage is already "
            f"complete; {detail}. Use a new directory for a fresh export."
        ]
    lines = [f"Resuming run {run_id} (started {started})."]
    if complete:
        lines.append(f"  complete: {', '.join(complete)}")
    for name, state in partial.items():
        lines.append(
            f"  partial:  {name} ({state.get('count')} records kept; "
            f"continues at unit {state.get('cursor')})"
        )
    if failed:
        lines.append(f"  failed:   {', '.join(failed)} (will be retried)")
    remaining = [n for n in STAGE_ORDER if n not in complete]
    lines.append(f"  to do:    {', '.join(remaining)}")
    return lines


def _apply_url(settings: JiraDCSettings, url: str) -> JiraDCSettings:
    """Use a validated command-line URL, but never send credentials to another host."""
    candidate = normalise_url(url)
    configured = normalise_url(settings.url)
    if url_origin(candidate) != url_origin(configured):
        raise ValueError(
            f"url {candidate} does not match the credentials file url {configured}: "
            "scheme, host, and port must be the same"
        )
    return settings.model_copy(update={"url": candidate})


def _preflight(settings: JiraDCSettings) -> bool:
    """Confirm config and credentials work before touching the output directory."""
    results = {result.name: result for result in run_checks(lambda: settings)}
    failed = next((r for r in results.values() if r.status == "fail"), None)
    if failed is not None:
        if failed.name == "config":
            error_console.print(
                f"[red]Jira configuration unavailable ({escape(failed.detail)})[/red]"
            )
        else:
            error_console.print(
                f"[red]Preflight check '{failed.name}' failed: "
                f"{escape(failed.detail)}[/red]"
            )
        error_console.print(
            "Run [bold]jira-config-export doctor[/bold] for the full report."
        )
        return False
    console.print(
        f"[green]✓[/green] Preflight: {escape(results['config'].detail)}; "
        "network reachable, credentials accepted",
        soft_wrap=True,
    )
    for result in results.values():
        if result.status == "warn":
            console.print(f"[yellow]![/yellow] {result.name}: {escape(result.detail)}")
    return True


def _save_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(data, sort_keys=True) + "\n")


def _skip_completed(
    stage: Stage,
    output_dir: Path,
    stages: dict[str, Any],
    coverage: dict[str, Any],
    manifest: dict[str, Any],
) -> int | None:
    """Reuse a completed stage's coverage and return its count, or None to run it."""
    previous = stages.get(stage.name)
    if not (
        isinstance(previous, dict)
        and previous.get("status") == StageStatus.COMPLETE
        and (output_dir / stage.filename).exists()
    ):
        return None
    coverage[stage.name] = previous
    _save_json(output_dir / "manifest.json", manifest)
    console.print(
        f"[dim]Skipping {stage.name}: already complete "
        f"({previous.get('count')} records)[/dim]"
    )
    count = previous.get("count")
    return count if isinstance(count, int) else 0


async def export_fields(
    base_url: str, output_dir: Path, *, user: str, password: str
) -> int:
    """Resume a completed field stage or retry it from the beginning."""
    stage = _STAGES_BY_NAME["fields"]
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    checkpoint_path = output_dir / "checkpoint.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        if checkpoint_path.exists():
            raise ValueError("Export metadata is incomplete")
        manifest = {
            "schema_version": 1,
            "run_id": str(uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
            "jira_version": None,
            "app_versions": {},
            "coverage": {},
        }
        _save_json(manifest_path, manifest)
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    else:
        checkpoint = {"stages": {}}
        _save_json(checkpoint_path, checkpoint)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or not isinstance(manifest.get("coverage"), dict)
        or not isinstance(checkpoint, dict)
        or not isinstance(checkpoint.get("stages"), dict)
    ):
        raise ValueError("Unsupported export metadata")
    stages = checkpoint["stages"]
    coverage = manifest["coverage"]
    assert isinstance(stages, dict)
    assert isinstance(coverage, dict)
    count = _skip_completed(stage, output_dir, stages, coverage, manifest)
    if count is not None:
        return count
    console.print(f"[cyan]Extracting[/cyan] {stage.name}... ", end="")
    try:
        count = await _extract_fields(
            base_url, output_dir, user=user, password=password
        )
    except (JiraError, ValueError, OSError) as exc:
        console.print()
        failure = _failure_result(exc, status=StageStatus.FAILED)
        stages[stage.name] = failure
        _save_json(checkpoint_path, checkpoint)
        coverage[stage.name] = failure
        _save_json(manifest_path, manifest)
        raise
    _print_dots(count)
    console.print()
    complete = {"status": StageStatus.COMPLETE, "count": count}
    stages[stage.name] = complete
    _save_json(checkpoint_path, checkpoint)
    coverage[stage.name] = complete
    _save_json(manifest_path, manifest)
    return count


async def _extract_fields(
    base_url: str, output_dir: Path, *, user: str, password: str
) -> int:
    """Read the field inventory and replace the fields file atomically."""
    async with _new_client(base_url, user=user, password=password) as client:
        records = await client.field_inventory()
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        output_dir / _STAGES_BY_NAME["fields"].filename,
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
    )
    return len(records)


async def _export_stage(
    client: JiraClient,
    stage: Stage,
    output_dir: Path,
    manifest: dict[str, Any],
    checkpoint: dict[str, Any],
) -> None:
    """Run one resumable stage, recording coverage, progress, and failures."""
    extract = stage.extract
    if extract is None:
        return
    manifest_path = output_dir / "manifest.json"
    checkpoint_path = output_dir / "checkpoint.json"
    stages = checkpoint["stages"]
    coverage = manifest["coverage"]
    if _skip_completed(stage, output_dir, stages, coverage, manifest) is not None:
        return
    blocker = next(
        (
            name
            for name in stage.prerequisites
            if stages.get(name, {}).get("status") != StageStatus.COMPLETE
        ),
        None,
    )
    if blocker is not None:
        coverage[stage.name] = {
            "status": StageStatus.NOT_QUERIED,
            "reason": f"{blocker} incomplete",
        }
        _save_json(manifest_path, manifest)
        return
    console.print(f"[cyan]Extracting[/cyan] {stage.name}... ", end="")
    progress = checkpoint.get("progress")
    if not isinstance(progress, dict):
        progress = checkpoint["progress"] = {}
    stage_file: StageFile | None = None
    try:
        stage_file = StageFile(output_dir / stage.filename, progress.get(stage.name))
        if stage_file.count:
            console.print(
                f"({stage_file.count} already written) ", end="", markup=False
            )
        with stage_file:
            async for batch in extract(client, output_dir, stage_file.cursor):
                _print_dots(stage_file.append(batch.records))
                progress[stage.name] = {
                    "cursor": batch.cursor,
                    "offset": stage_file.offset,
                    "count": stage_file.count,
                }
                _save_json(checkpoint_path, checkpoint)
            stage_file.ensure_exists()
    except (JiraError, ValueError, OSError) as exc:
        console.print()
        result = _tag_api(stage.name, _failure_result(exc))
        if stage_file is not None and stage_file.count:
            result["records_written"] = stage_file.count
        stages[stage.name] = result
        coverage[stage.name] = result
        _save_json(checkpoint_path, checkpoint)
        _save_json(manifest_path, manifest)
        return
    console.print()
    result = _tag_api(
        stage.name, {"status": StageStatus.COMPLETE, "count": stage_file.count}
    )
    stages[stage.name] = result
    progress.pop(stage.name, None)
    _save_json(checkpoint_path, checkpoint)
    coverage[stage.name] = result
    _save_json(manifest_path, manifest)


async def export_configuration(
    base_url: str, output_dir: Path, *, user: str, password: str
) -> None:
    """Export configuration stages and report unsupported app capabilities."""
    manifest_path = output_dir / "manifest.json"
    checkpoint_path = output_dir / "checkpoint.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    coverage = manifest["coverage"]
    for gap in _DECLARED_GAPS:
        coverage[gap.name] = {
            "status": StageStatus.UNSUPPORTED,
            "reason": gap.reason,
            "evidence": gap.evidence,
        }
    _save_json(manifest_path, manifest)
    async with _new_client(base_url, user=user, password=password) as client:
        if manifest.get("jira_version") is None:
            try:
                version = await client.server_version()
                if version is not None:
                    manifest["jira_version"] = version
                    _save_json(manifest_path, manifest)
            except JiraError, ValueError:
                pass  # Version is optional; do not expose server response content.
        for stage in _RESUMABLE_STAGES:
            await _export_stage(client, stage, output_dir, manifest, checkpoint)

    if coverage.get("app_inventory", {}).get("status") == StageStatus.COMPLETE:
        manifest["app_versions"] = _app_versions(output_dir)
    else:
        manifest["app_versions"] = {}
    _save_json(manifest_path, manifest)


def _print_summary(output_dir: Path, *, fields_failed: bool = False) -> bool:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return True
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    coverage = manifest["coverage"]
    queried = tuple(stage.name for stage in _RESUMABLE_STAGES)
    if fields_failed:
        for stage in queried:
            coverage.setdefault(
                stage,
                {
                    "status": StageStatus.NOT_QUERIED,
                    "reason": "fields incomplete",
                },
            )
        _save_json(manifest_path, manifest)
    advisory = {stage.name for stage in _RESUMABLE_STAGES if stage.advisory}
    table = Table(title="Coverage summary", box=None, pad_edge=False)
    table.add_column("Stage", style="cyan")
    table.add_column("Status")
    for stage, result in sorted(coverage.items()):
        status = StageStatus(result["status"])
        suffix = (
            f" ({result['count']} records"
            + (", not found" if result["count"] == 0 else "")
            + ")"
            if status is StageStatus.COMPLETE
            else ""
        )
        style = (
            "green"
            if status is StageStatus.COMPLETE
            else "yellow"
            if status is StageStatus.NOT_QUERIED or stage in advisory
            else "red"
        )
        table.add_row(stage, f"[{style}]{status.value}{suffix}[/{style}]")
    console.print(table)
    return fields_failed or any(
        StageStatus(coverage[stage]["status"]) is not StageStatus.COMPLETE
        for stage in queried
        if stage not in advisory
    )


def _load_settings(url: str | None) -> JiraDCSettings | None:
    """Load credentials and apply an optional, validated command-line URL."""
    try:
        settings = load_jira_dc_settings()
    except (OSError, ValueError) as exc:
        error_console.print(
            f"[red]Jira configuration unavailable ({_error_text(exc)})[/red]"
        )
        error_console.print(
            "Run [bold]jira-config-export doctor[/bold] for the full report."
        )
        return None
    if url is None:
        return settings
    try:
        return _apply_url(settings, url)
    except ValueError as exc:
        error_console.print(f"[red]{escape(str(exc))}[/red]")
        return None


def _run_export(output_dir: Path, *, url: str | None = None) -> int:
    """Export Jira Data Center configuration to OUTPUT_DIR."""
    settings = _load_settings(url)
    if settings is None:
        return 1
    if not _preflight(settings):
        return 1
    console.print(
        f"Output directory: {output_dir.resolve()}", markup=False, soft_wrap=True
    )
    for line in _describe_state(output_dir):
        console.print(line, markup=False, soft_wrap=True)
    auth = {"user": settings.user, "password": settings.password.get_secret_value()}
    try:
        asyncio.run(export_fields(settings.url, output_dir, **auth))
    except JiraError as exc:
        if exc.status_code is not None:
            error_console.print(
                f"[red]Jira field endpoint failed HTTP {exc.status_code}[/red]"
            )
        else:
            error_console.print(
                f"[red]Jira field export failed ({_error_text(exc)})[/red]"
            )
        _print_summary(output_dir, fields_failed=True)
        return 1
    except (ValueError, OSError) as exc:
        error_console.print(f"[red]Jira field export failed ({_error_text(exc)})[/red]")
        _print_summary(output_dir, fields_failed=True)
        return 1
    try:
        asyncio.run(export_configuration(settings.url, output_dir, **auth))
    except (JiraError, ValueError, OSError) as exc:
        error_console.print(
            f"[red]Jira configuration export failed ({_error_text(exc)})[/red]"
        )
        return 1
    return int(_print_summary(output_dir))


@app.command()
def export(
    output_dir: Path,
    url: Annotated[
        str | None,
        typer.Option(
            "--url",
            help=(
                "Jira base URL. Overrides the url in the credentials file, whose "
                "scheme, host, and port it must match. Credentials always come "
                "from the credentials file."
            ),
        ),
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Log debug details to stderr.")
    ] = False,
) -> None:
    """Export Jira Data Center configuration to OUTPUT_DIR."""
    _configure_logging(verbose=verbose)
    raise typer.Exit(_run_export(output_dir, url=url))


_DOCTOR_STYLES = {"pass": "green", "warn": "yellow", "fail": "red", "skip": "dim"}


def _run_doctor() -> int:
    results = run_checks(load_jira_dc_settings)
    table = Table(title="Doctor", box=None, pad_edge=False)
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Detail")
    for result in results:
        style = _DOCTOR_STYLES[result.status]
        table.add_row(
            result.name,
            f"[{style}]{result.status}[/{style}]",
            escape(result.detail),
        )
    console.print(table)
    return int(any(result.status == "fail" for result in results))


@app.command()
def doctor() -> None:
    """Check config, DNS, TCP, TLS, and credentials for the Jira DC connection."""
    raise typer.Exit(_run_doctor())


def main(argv: list[str] | None = None) -> int:
    """Run the Typer command and return an exit status for the console script."""
    try:
        app(args=argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
