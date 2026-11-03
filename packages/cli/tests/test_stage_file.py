"""StageFile commits whole lines durably and resumes from a checkpointed offset."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cli.stage_file import StageFile

if TYPE_CHECKING:
    from pathlib import Path


def _record(identifier: str) -> dict[str, Any]:
    return {"source": {"id": identifier}, "value": identifier}


def _progress(stage_file: StageFile, cursor: int) -> dict[str, int]:
    return {"cursor": cursor, "offset": stage_file.offset, "count": stage_file.count}


def test_no_file_exists_until_a_batch_is_committed(tmp_path: Path) -> None:
    path = tmp_path / "stage.jsonl"

    with StageFile(path) as stage_file:
        assert not path.exists()
        stage_file.ensure_exists()

    assert path.read_bytes() == b""


def test_resume_discards_uncommitted_and_torn_bytes(tmp_path: Path) -> None:
    path = tmp_path / "stage.jsonl"
    with StageFile(path) as first:
        first.append([_record("a")])
        committed = _progress(first, cursor=1)
        first.append([_record("b")])  # written but never checkpointed
    with path.open("ab") as stream:
        stream.write(b'{"source": {"id": "c"')  # torn write

    with StageFile(path, committed) as resumed:
        assert resumed.cursor == 1
        assert resumed.count == 1
        resumed.append([_record("a"), _record("b")])  # "a" is already committed

    assert [line.split(b'"')[5] for line in path.read_bytes().splitlines()] == [
        b"a",
        b"b",
    ]
    assert path.read_bytes().endswith(b"\n")


def test_inconsistent_progress_restarts_from_scratch(tmp_path: Path) -> None:
    path = tmp_path / "stage.jsonl"
    with StageFile(path) as first:
        first.append([_record("a")])

    stale = {"cursor": 5, "offset": 10_000, "count": 3}
    with StageFile(path, stale) as restarted:
        assert (restarted.cursor, restarted.offset, restarted.count) == (0, 0, 0)

    assert not path.exists()


def test_progress_with_mismatched_record_count_restarts(tmp_path: Path) -> None:
    path = tmp_path / "stage.jsonl"
    with StageFile(path) as first:
        first.append([_record("a")])
        progress = _progress(first, cursor=1)

    progress["count"] = 2
    with StageFile(path, progress) as restarted:
        assert restarted.cursor == 0
