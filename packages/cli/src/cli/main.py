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
from config import load_jira_dc_settings
from jira import (
    IssueTypeScreenTarget,
    JiraClient,
    JiraError,
    ProjectRef,
    ScreenTab,
    describe_exception,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from jira import Batch

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


def _failure_result(exc: Exception, *, status: str | None = None) -> dict[str, Any]:
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
        result = {"status": status or "failed", "error": type(exc).__name__}
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
    """

    name: str
    filename: str
    extract: ExtractFunc | None = None
    prerequisites: tuple[str, ...] = ()
    internal_api: bool = False


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
    return _source_ids(output_dir / "fields.jsonl")


def _screen_ids(output_dir: Path) -> list[str]:
    """Read the screen IDs written by the screens stage."""
    return _source_ids(output_dir / "screens.jsonl")


def _tabs(output_dir: Path) -> list[ScreenTab]:
    """Read the screen and tab IDs written by the tabs stage."""
    return [
        ScreenTab(record["screen_id"], record["source"]["id"].split(":", 1)[1])
        for record in _read_jsonl(output_dir / "screen-tabs.jsonl")
    ]


def _custom_fields(
    client: JiraClient, output_dir: Path, start: int
) -> AsyncIterator[Batch]:
    return client.custom_fields(sorted(_field_ids(output_dir)), start)


def _custom_field_options(
    client: JiraClient, output_dir: Path, start: int
) -> AsyncIterator[Batch]:
    return client.custom_field_options(_field_ids(output_dir), start)


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
        for record in _read_jsonl(output_dir / "projects.jsonl")
    ]
    return client.project_workflow_schemes(projects, start)


def _issue_type_screen_targets(output_dir: Path) -> list[IssueTypeScreenTarget]:
    """Pair each project with the issue types its issue type scheme offers."""
    issue_types_by_project: dict[str, set[str]] = {}
    for record in _read_jsonl(output_dir / "issue-type-schemes.jsonl"):
        scheme = record["issue_type_scheme"]
        for project_id in scheme["project_ids"]:
            issue_types_by_project.setdefault(project_id, set()).update(
                scheme["issue_type_ids"]
            )
    targets: list[IssueTypeScreenTarget] = []
    for record in _read_jsonl(output_dir / "projects.jsonl"):
        project = ProjectRef(record["source"]["id"], record["project"]["key"])
        if project.project_id not in issue_types_by_project:
            message = "Project is not in any issue type scheme"
            raise JiraError(
                message, error_type="ValueError", stage_status="failed", detail=message
            )
        targets.extend(
            IssueTypeScreenTarget(project, issue_type_id)
            for issue_type_id in sorted(
                issue_types_by_project[project.project_id], key=lambda i: (len(i), i)
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

# Sources with no supported read-only route: declared gaps, not queried stages.
_DECLARED_GAPS = {
    "elements_connect": (
        "no supported read-only Elements Connect DC config API identified"
    ),
    "dynamic_forms": "no supported read-only Dynamic Forms DC REST API identified",
    "scriptrunner": "no supported read-only ScriptRunner DC REST API identified",
}


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
            n for n in STAGE_ORDER if stages.get(n, {}).get("status") == "complete"
        ]
        failed = [n for n in STAGE_ORDER if stages.get(n, {}).get("status") == "failed"]
        partial = {
            n: progress[n] for n in STAGE_ORDER if isinstance(progress.get(n), dict)
        }
    except OSError, ValueError, KeyError, TypeError, AttributeError:
        return [
            "Found existing output that could not be read; the export will report why."
        ]
    if len(complete) == len(STAGE_ORDER):
        return [
            f"Resuming run {run_id} (started {started}): every stage is already "
            "complete, so nothing will be fetched. "
            "Use a new directory for a fresh export."
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


def _preflight() -> bool:
    """Confirm config and credentials work before touching the output directory."""
    results = {result.name: result for result in run_checks(load_jira_dc_settings)}
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
    previous = stages.get(stage.name)
    if (
        isinstance(previous, dict)
        and previous.get("status") == "complete"
        and (output_dir / stage.filename).exists()
    ):
        coverage[stage.name] = previous
        _save_json(manifest_path, manifest)
        console.print(
            f"[dim]Skipping {stage.name}: already complete "
            f"({previous['count']} records)[/dim]"
        )
        return previous["count"]
    console.print(f"[cyan]Extracting[/cyan] {stage.name}... ", end="")
    try:
        count = await _extract_fields(
            base_url, output_dir, user=user, password=password
        )
    except (JiraError, ValueError, OSError) as exc:
        console.print()
        failure = _failure_result(exc, status="failed")
        stages[stage.name] = failure
        _save_json(checkpoint_path, checkpoint)
        coverage[stage.name] = failure
        _save_json(manifest_path, manifest)
        raise
    _print_dots(count)
    console.print()
    complete = {"status": "complete", "count": count}
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
    previous = stages.get(stage.name)
    if (
        isinstance(previous, dict)
        and previous.get("status") == "complete"
        and (output_dir / stage.filename).exists()
    ):
        coverage[stage.name] = previous
        _save_json(manifest_path, manifest)
        console.print(
            f"[dim]Skipping {stage.name}: already complete "
            f"({previous.get('count')} records)[/dim]"
        )
        return
    blocker = next(
        (
            name
            for name in stage.prerequisites
            if stages.get(name, {}).get("status") != "complete"
        ),
        None,
    )
    if blocker is not None:
        coverage[stage.name] = {
            "status": "not_queried",
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
    result = _tag_api(stage.name, {"status": "complete", "count": stage_file.count})
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
    for name, reason in _DECLARED_GAPS.items():
        coverage[name] = {"status": "unsupported", "reason": reason}
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
                stage, {"status": "not_queried", "reason": "fields incomplete"}
            )
        _save_json(manifest_path, manifest)
    table = Table(title="Coverage summary", box=None, pad_edge=False)
    table.add_column("Stage", style="cyan")
    table.add_column("Status")
    for stage, result in sorted(coverage.items()):
        status = result["status"]
        suffix = (
            f" ({result['count']} records"
            + (", not found" if result["count"] == 0 else "")
            + ")"
            if status == "complete"
            else ""
        )
        style = (
            "green"
            if status == "complete"
            else "yellow"
            if status == "not_queried"
            else "red"
        )
        table.add_row(stage, f"[{style}]{status}{suffix}[/{style}]")
    console.print(table)
    return fields_failed or any(
        coverage[stage]["status"] != "complete" for stage in queried
    )


def _run_export(output_dir: Path) -> int:
    """Export Jira Data Center configuration to OUTPUT_DIR."""
    if not _preflight():
        return 1
    console.print(
        f"Output directory: {output_dir.resolve()}", markup=False, soft_wrap=True
    )
    for line in _describe_state(output_dir):
        console.print(line, markup=False, soft_wrap=True)
    try:
        settings = load_jira_dc_settings()
    except (OSError, ValueError) as exc:
        error_console.print(
            f"[red]Jira configuration unavailable ({_error_text(exc)})[/red]"
        )
        return 1
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
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Log debug details to stderr.")
    ] = False,  # noqa: FBT002
) -> None:
    """Export Jira Data Center configuration to OUTPUT_DIR."""
    _configure_logging(verbose=verbose)
    raise typer.Exit(_run_export(output_dir))


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
