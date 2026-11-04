"""Documented stage, ordering, and gap claims stay tied to the implementation."""

from __future__ import annotations

import re
from pathlib import Path

from cli import main as cli

_ROOT = Path(__file__).parents[3]
_REFERENCE = _ROOT / "docs" / "reference" / "cli.md"


def _section(text: str, heading: str, next_heading: str) -> str:
    return text.split(heading, 1)[1].split(next_heading, 1)[0]


def test_output_file_table_lists_every_stage_and_file() -> None:
    section = _section(_REFERENCE.read_text(), "## Output files", "\n## ")
    documented = set(
        re.findall(r"(?m)^\| `([a-z0-9-]+\.jsonl)` \| `?([a-z_]+)`? \|", section)
    )

    assert documented == {(stage.filename, stage.name) for stage in cli.STAGES}


def test_record_order_table_documents_every_stage() -> None:
    text = _REFERENCE.read_text()
    section = _section(text, "### Record order", "\n### ")
    rows = re.findall(r"(?m)^\| `([a-z_]+)` \| ([^|]+) \|$", section)

    assert {stage for stage, _ in rows} == set(cli.STAGE_ORDER)
    assert all(order.strip() for _, order in rows)


def test_declared_gap_names_appear_in_both_readmes() -> None:
    for relative in ("README.md", "packages/cli/README.md"):
        text = (_ROOT / relative).read_text()
        for name in {gap.name for gap in cli._DECLARED_GAPS}:
            assert f"`{name}`" in text, f"{name} is missing from {relative}"
