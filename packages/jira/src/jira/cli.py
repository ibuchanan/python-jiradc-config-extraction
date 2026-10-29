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
        return 1
    except (httpx.RequestError, ValueError, OSError) as exc:
        print(f"Jira field export failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"{count} fields exported to {args.output_dir / 'fields.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
