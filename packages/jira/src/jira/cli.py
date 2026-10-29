"""Export Jira Data Center custom fields as JSON Lines."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path

import httpx

from .client import JiraClient

FIELD_PATH = "/rest/api/2/field"


async def export_fields(base_url: str, output_dir: Path, token: str) -> int:
    """Read the field inventory and write custom fields to fields.jsonl."""
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
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "fields.jsonl").write_text(
        "".join(
            json.dumps(field, ensure_ascii=False) + "\n" for field in custom_fields
        ),
        encoding="utf-8",
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
