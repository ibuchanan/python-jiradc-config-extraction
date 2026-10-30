"""Export Jira Data Center custom fields as JSON Lines."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from .client import JiraClient

FIELD_PATH = "/rest/api/2/field"
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:password|passwd|token|secret|api[_-]?key|credential|authorization)\b\s*[:=]"
)


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


async def export_fields(base_url: str, output_dir: Path, token: str) -> int:
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
        count = await _extract_fields(base_url, output_dir, token)
    except (httpx.HTTPError, ValueError, OSError) as exc:
        failure: dict[str, object] = {"status": "failed", "error": type(exc).__name__}
        if isinstance(exc, httpx.HTTPStatusError):
            failure["http_status"] = exc.response.status_code
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


async def _extract_fields(base_url: str, output_dir: Path, token: str) -> int:
    """Read the field inventory and replace fields.jsonl atomically."""
    async with JiraClient(base_url, token) as client:
        response = await client.get(FIELD_PATH)
    fields = response.json()
    if not isinstance(fields, list):
        raise ValueError("Jira field inventory was not a list")
    custom_fields = [
        field
        for field in fields
        if isinstance(field, dict) and field.get("custom") is True
    ]
    if any(
        not isinstance(field.get("id"), str) or not field["id"]
        for field in custom_fields
    ):
        raise ValueError("Jira custom field missing a stable id")
    by_id = {field["id"]: field for field in custom_fields}
    custom_fields = [by_id[field_id] for field_id in sorted(by_id)]
    output_dir.mkdir(parents=True, exist_ok=True)
    extracted_at = datetime.now(UTC).isoformat()
    _atomic_write(
        output_dir / "fields.jsonl",
        "".join(
            json.dumps(
                {
                    "schema_version": 1,
                    "source": {
                        "system": "jira",
                        "entity": "field",
                        "id": field["id"],
                        "endpoint": FIELD_PATH,
                    },
                    "extracted_at": extracted_at,
                    "field": {
                        "name": (
                            "[REDACTED]"
                            if isinstance(field.get("name"), str)
                            and SECRET_ASSIGNMENT.search(field["name"])
                            else field.get("name")
                        )
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
            for field in custom_fields
        ),
    )
    return len(custom_fields)


async def export_configuration(base_url: str, output_dir: Path, token: str) -> None:
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
    async with JiraClient(base_url, token) as client:
        if manifest.get("jira_version") is None:
            try:
                info = (await client.get("/rest/api/2/serverInfo")).json()
                if isinstance(info, dict) and isinstance(info.get("version"), str):
                    manifest["jira_version"] = info["version"]
                    _save_json(manifest_path, manifest)
            except httpx.HTTPError, ValueError:
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
            print(f"Extracting {stage}...")
            try:
                records = await extract(client, output_dir)
                _atomic_write(
                    output_dir / filename,
                    "".join(
                        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                        for record in records
                    ),
                )
            except (
                httpx.HTTPStatusError,
                httpx.RequestError,
                ValueError,
                OSError,
            ) as exc:
                status = (
                    "unauthorized"
                    if isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code in (401, 403)
                    else "unsupported"
                    if isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code in (404, 405, 501)
                    else "failed"
                )
                result: dict[str, Any] = {"status": status, "error": type(exc).__name__}
                if isinstance(exc, httpx.HTTPStatusError):
                    result["http_status"] = exc.response.status_code
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


def _record(
    entity: str, identifier: str, endpoint: str, **data: object
) -> dict[str, Any]:
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


async def _field_applicability(
    client: JiraClient, output_dir: Path
) -> list[dict[str, Any]]:
    endpoint = "/rest/api/2/customFields"
    fields = {
        json.loads(line)["source"]["id"]
        for line in (output_dir / "fields.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    }
    by_id: dict[str, dict[str, Any]] = {}
    start = 0
    while True:
        payload = (await client.get(endpoint, params={"startAt": start})).json()
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
            if field["id"] in fields:
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
    return [by_id[key] for key in sorted(by_id)]


async def _field_options(client: JiraClient, output_dir: Path) -> list[dict[str, Any]]:
    field_ids = [
        json.loads(line)["source"]["id"]
        for line in (output_dir / "fields.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    by_id: dict[tuple[str, str], dict[str, Any]] = {}
    for field_id in field_ids:
        if (
            not field_id.startswith("customfield_")
            or not field_id.removeprefix("customfield_").isdigit()
        ):
            raise ValueError("Invalid custom field id")
        endpoint = (
            f"/rest/api/2/customFields/{field_id.removeprefix('customfield_')}/options"
        )
        page = 1
        seen = 0
        while True:
            payload = (
                await client.get(
                    endpoint, params={"page": page, "useAllContexts": True}
                )
            ).json()
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
                    value="[REDACTED]"
                    if isinstance(value, str) and SECRET_ASSIGNMENT.search(value)
                    else value,
                    disabled=option.get("disabled"),
                    child_ids=[str(child) for child in option.get("childrenIds", [])],
                )
            seen += len(options)
            if seen >= payload["total"]:
                break
            if not options:
                raise ValueError("Empty options page before total")
            page += 1
    return [by_id[key] for key in sorted(by_id)]


def _screen_ids(output_dir: Path, filename: str) -> list[str]:
    return [
        json.loads(line)["source"]["id"]
        for line in (output_dir / filename).read_text(encoding="utf-8").splitlines()
    ]


async def _screens(client: JiraClient, _output_dir: Path) -> list[dict[str, Any]]:
    endpoint = "/rest/api/2/screens"
    by_id: dict[str, dict[str, Any]] = {}
    start = 0
    while True:
        payload = (await client.get(endpoint, params={"startAt": start})).json()
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("values"), list)
            or not isinstance(payload.get("total"), int)
        ):
            raise ValueError("Invalid screens page")
        values = payload["values"]
        for screen in values:
            if not isinstance(screen, dict) or not isinstance(screen.get("id"), int):
                raise ValueError("Invalid screen identifier")
            identifier = str(screen["id"])
            name = screen.get("name")
            by_id[identifier] = _record(
                "screen",
                identifier,
                endpoint,
                screen={
                    "name": "[REDACTED]"
                    if isinstance(name, str) and SECRET_ASSIGNMENT.search(name)
                    else name
                },
            )
        start += len(values)
        if start >= payload["total"]:
            break
        if not values:
            raise ValueError("Empty screens page before total")
    return [by_id[key] for key in sorted(by_id, key=int)]


async def _screen_tabs(client: JiraClient, output_dir: Path) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for screen_id in _screen_ids(output_dir, "screens.jsonl"):
        endpoint = f"/rest/api/2/screens/{screen_id}/tabs"
        tabs = (await client.get(endpoint)).json()
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
                name="[REDACTED]"
                if isinstance(name, str) and SECRET_ASSIGNMENT.search(name)
                else name,
            )
    return [by_id[key] for key in sorted(by_id)]


async def _screen_placements(
    client: JiraClient, output_dir: Path
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for tab in [
        json.loads(line)
        for line in (output_dir / "screen-tabs.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]:
        screen_id, tab_id = tab["screen_id"], tab["source"]["id"].split(":", 1)[1]
        endpoint = f"/rest/api/2/screens/{screen_id}/tabs/{tab_id}/fields"
        fields = (await client.get(endpoint)).json()
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
    print("Coverage summary:")
    for stage, result in sorted(coverage.items()):
        status = result["status"]
        suffix = (
            f" ({result['count']} records"
            + (", not found" if result["count"] == 0 else "")
            + ")"
            if status == "complete"
            else ""
        )
        print(f"  {stage}: {status}{suffix}")
    return fields_failed or any(
        coverage[stage]["status"] != "complete" for stage in queried
    )


def main(argv: list[str] | None = None) -> int:
    """Run a read-only export, reporting errors without printing credentials."""
    parser = argparse.ArgumentParser(
        description="Export Jira Data Center custom fields"
    )
    parser.add_argument("base_url", help="Jira Data Center base URL")
    parser.add_argument("output_dir", type=Path, help="Directory for fields.jsonl")
    args = parser.parse_args(argv)
    token = os.environ.get("JIRA_PAT") or getpass.getpass("Jira PAT: ")
    if not token:
        print("Jira PAT is required", file=sys.stderr)
        return 1
    try:
        count = asyncio.run(export_fields(args.base_url, args.output_dir, token))
    except httpx.HTTPStatusError as exc:
        print(
            f"Jira {FIELD_PATH} returned HTTP {exc.response.status_code}",
            file=sys.stderr,
        )
        _print_summary(args.output_dir, fields_failed=True)
        return 1
    except (httpx.RequestError, ValueError, OSError) as exc:
        print(f"Jira field export failed ({type(exc).__name__})", file=sys.stderr)
        _print_summary(args.output_dir, fields_failed=True)
        return 1
    print(f"{count} fields exported to {args.output_dir / 'fields.jsonl'}")
    try:
        asyncio.run(export_configuration(args.base_url, args.output_dir, token))
    except (httpx.HTTPError, ValueError, OSError) as exc:
        print(
            f"Jira configuration export failed ({type(exc).__name__})", file=sys.stderr
        )
        return 1
    return int(_print_summary(args.output_dir))


if __name__ == "__main__":
    sys.exit(main())
