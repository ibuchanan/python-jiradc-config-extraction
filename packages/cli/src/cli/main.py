"""Export Jira Data Center custom fields as JSON Lines."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import typer
from rich.console import Console
from rich.table import Table

from config import load_jira_dc_settings
from jira import JiraClient, JiraError

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


def _save_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(data, sort_keys=True) + "\n")


async def export_fields(
    base_url: str, output_dir: Path, *, user: str, password: str
) -> int:
    """Resume a completed field stage or retry it from the beginning."""
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
    previous = stages.get("fields")
    if (
        isinstance(previous, dict)
        and previous.get("status") == "complete"
        and (output_dir / "fields.jsonl").exists()
    ):
        coverage["fields"] = previous
        _save_json(manifest_path, manifest)
        return previous["count"]
    try:
        count = await _extract_fields(
            base_url, output_dir, user=user, password=password
        )
    except (JiraError, ValueError, OSError) as exc:
        if isinstance(exc, JiraError):
            failure: dict[str, object] = {
                "status": "failed",
                "error": exc.error_type,
            }
            if exc.status_code is not None:
                failure["http_status"] = exc.status_code
        else:
            failure = {"status": "failed", "error": type(exc).__name__}
        stages["fields"] = failure
        _save_json(checkpoint_path, checkpoint)
        coverage["fields"] = failure
        _save_json(manifest_path, manifest)
        raise
    complete = {"status": "complete", "count": count}
    stages["fields"] = complete
    _save_json(checkpoint_path, checkpoint)
    coverage["fields"] = complete
    _save_json(manifest_path, manifest)
    return count


async def _extract_fields(
    base_url: str, output_dir: Path, *, user: str, password: str
) -> int:
    """Read the field inventory and replace fields.jsonl atomically."""
    async with JiraClient(base_url, user=user, password=password) as client:
        records = await client.field_inventory()
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        output_dir / "fields.jsonl",
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
    )
    return len(records)


async def export_configuration(  # noqa: PLR0915
    base_url: str, output_dir: Path, *, user: str, password: str
) -> None:
    """Export supported field applicability/options; report unsupported capabilities."""
    manifest_path = output_dir / "manifest.json"
    checkpoint_path = output_dir / "checkpoint.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    stages = checkpoint["stages"]
    coverage = manifest["coverage"]
    for name in (
        "explicit_field_contexts",
        "field_configurations",
        "screen_schemes",
        "issue_type_screen_schemes",
    ):
        coverage[name] = {
            "status": "unsupported",
            "reason": "not exposed by bundled Jira DC 11.2.1 REST API",
        }
    coverage["elements_connect"] = {
        "status": "unsupported",
        "reason": "no supported read-only Elements Connect DC config API identified",
    }
    coverage["dynamic_forms"] = {
        "status": "unsupported",
        "reason": "no supported read-only Dynamic Forms DC REST API identified",
    }
    coverage["scriptrunner"] = {
        "status": "unsupported",
        "reason": "no supported read-only ScriptRunner DC REST API identified",
    }
    _save_json(manifest_path, manifest)
    async with JiraClient(base_url, user=user, password=password) as client:
        if manifest.get("jira_version") is None:
            try:
                version = await client.server_version()
                if version is not None:
                    manifest["jira_version"] = version
                    _save_json(manifest_path, manifest)
            except JiraError, ValueError:
                pass  # Version is optional; do not expose server response content.
        for stage, filename, extract in (
            ("field_applicability", "field-contexts.jsonl", _field_applicability),
            ("field_options", "field-options.jsonl", _field_options),
            ("screens", "screens.jsonl", _screens),
            ("screen_tabs", "screen-tabs.jsonl", _screen_tabs),
            ("screen_placements", "screen-placements.jsonl", _screen_placements),
        ):
            previous = stages.get(stage)
            if (
                isinstance(previous, dict)
                and previous.get("status") == "complete"
                and (output_dir / filename).exists()
            ):
                coverage[stage] = previous
                _save_json(manifest_path, manifest)
                continue
            prerequisite = {
                "screen_tabs": "screens",
                "screen_placements": "screen_tabs",
            }.get(stage)
            if (
                prerequisite
                and stages.get(prerequisite, {}).get("status") != "complete"
            ):
                coverage[stage] = {
                    "status": "not_queried",
                    "reason": f"{prerequisite} incomplete",
                }
                _save_json(manifest_path, manifest)
                continue
            console.print(f"[cyan]Extracting[/cyan] {stage}...")
            try:
                records = await extract(client, output_dir)
                _atomic_write(
                    output_dir / filename,
                    "".join(
                        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                        for record in records
                    ),
                )
            except (JiraError, ValueError, OSError) as exc:
                if isinstance(exc, JiraError):
                    status = exc.stage_status
                    result: dict[str, Any] = {"status": status, "error": exc.error_type}
                    if exc.status_code is not None:
                        result["http_status"] = exc.status_code
                else:
                    status = "failed"
                    result = {"status": status, "error": type(exc).__name__}
                stages[stage] = result
                coverage[stage] = result
                _save_json(checkpoint_path, checkpoint)
                _save_json(manifest_path, manifest)
                continue
            result = {"status": "complete", "count": len(records)}
            stages[stage] = result
            _save_json(checkpoint_path, checkpoint)
            coverage[stage] = result
            _save_json(manifest_path, manifest)


async def _field_applicability(
    client: JiraClient, output_dir: Path
) -> list[dict[str, Any]]:
    fields = {
        json.loads(line)["source"]["id"]
        for line in (output_dir / "fields.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    }
    field_ids = sorted(fields)
    records = await client.field_applicability(field_ids)
    return records


async def _field_options(client: JiraClient, output_dir: Path) -> list[dict[str, Any]]:
    field_ids = [
        json.loads(line)["source"]["id"]
        for line in (output_dir / "fields.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    records = await client.field_options(field_ids)
    return records


def _screen_ids(output_dir: Path, filename: str) -> list[str]:
    return [
        json.loads(line)["source"]["id"]
        for line in (output_dir / filename).read_text(encoding="utf-8").splitlines()
    ]


async def _screens(client: JiraClient, _output_dir: Path) -> list[dict[str, Any]]:
    records = await client.screens()
    return records


async def _screen_tabs(client: JiraClient, output_dir: Path) -> list[dict[str, Any]]:
    screen_ids = _screen_ids(output_dir, "screens.jsonl")
    records = await client.screen_tabs(screen_ids)
    return records


async def _screen_placements(
    client: JiraClient, output_dir: Path
) -> list[dict[str, Any]]:
    tabs_list = [
        json.loads(line)
        for line in (output_dir / "screen-tabs.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    tabs = [
        (tab["screen_id"], tab["source"]["id"].split(":", 1)[1]) for tab in tabs_list
    ]
    records = await client.screen_placements(tabs)
    return records


def _print_summary(output_dir: Path, *, fields_failed: bool = False) -> bool:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return True
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    coverage = manifest["coverage"]
    queried = (
        "field_applicability",
        "field_options",
        "screens",
        "screen_tabs",
        "screen_placements",
    )
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
    try:
        settings = load_jira_dc_settings()
    except (OSError, ValueError) as exc:
        error_console.print(
            f"[red]Jira configuration unavailable ({type(exc).__name__})[/red]"
        )
        return 1
    auth = {"user": settings.user, "password": settings.password.get_secret_value()}
    try:
        count = asyncio.run(export_fields(settings.url, output_dir, **auth))
    except JiraError as exc:
        if exc.status_code is not None:
            error_console.print(
                f"[red]Jira field endpoint failed HTTP {exc.status_code}[/red]"
            )
        else:
            error_console.print(
                f"[red]Jira field export failed ({exc.error_type})[/red]"
            )
        _print_summary(output_dir, fields_failed=True)
        return 1
    except (ValueError, OSError) as exc:
        error_console.print(
            f"[red]Jira field export failed ({type(exc).__name__})[/red]"
        )
        _print_summary(output_dir, fields_failed=True)
        return 1
    console.print(
        f"{count} fields exported to {output_dir / 'fields.jsonl'}",
        style="green",
        markup=False,
    )
    try:
        asyncio.run(export_configuration(settings.url, output_dir, **auth))
    except (JiraError, ValueError, OSError) as exc:
        error_console.print(
            f"[red]Jira configuration export failed ({type(exc).__name__})[/red]"
        )
        return 1
    return int(_print_summary(output_dir))


@app.command()
def export(output_dir: Path) -> None:
    """Export Jira Data Center configuration to OUTPUT_DIR."""
    raise typer.Exit(_run_export(output_dir))


def main(argv: list[str] | None = None) -> int:
    """Run the Typer command and return an exit status for the console script."""
    try:
        app(args=argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
