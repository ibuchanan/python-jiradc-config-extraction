"""Append-only JSON Lines output that can resume after a crash.

The commit protocol is: append whole lines, flush, ``fsync``, and only then let the
caller record ``offset`` (and its own cursor) in the checkpoint. Bytes beyond the
last recorded offset are therefore never trusted: on resume they are truncated, so
a torn final line or a batch whose checkpoint was never saved is simply redone.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, BinaryIO, Self

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path
    from types import TracebackType


class StageFile:
    """One stage's JSONL file, opened for appending from a committed position.

    Attributes:
        cursor: Where the extractor should resume (0 when starting fresh).
        offset: Byte length of the committed, fsynced content.
        count: Number of committed records.
    """

    def __init__(self, path: Path, progress: object = None) -> None:
        self.path = path
        self.seen: set[str] = set()
        self.cursor: int = 0
        self.offset: int = 0
        self.count: int = 0
        if not self._resume(progress):
            self.seen.clear()
            self.cursor = self.offset = self.count = 0
        self._stream: BinaryIO | None = None
        if self.offset:
            self._stream = path.open("r+b")
            self._stream.truncate(self.offset)
            self._stream.seek(self.offset)
        else:
            # Fresh start: drop any stale file; a new one appears with the first batch.
            path.unlink(missing_ok=True)

    def _resume(self, progress: object) -> bool:
        """Adopt checkpointed progress if the file still matches it."""
        if not isinstance(progress, dict) or not self.path.exists():
            return False
        cursor = progress.get("cursor")
        offset = progress.get("offset")
        count = progress.get("count")
        if not (
            isinstance(cursor, int)
            and isinstance(offset, int)
            and isinstance(count, int)
            and min(cursor, offset, count) >= 0
        ):
            return False
        if self.path.stat().st_size < offset:
            return False
        with self.path.open("rb") as stream:
            committed = stream.read(offset)
        try:
            ids = [json.loads(line)["source"]["id"] for line in committed.splitlines()]
        except ValueError, KeyError, TypeError:
            return False
        if len(ids) != count or (committed and not committed.endswith(b"\n")):
            return False
        self.seen.update(ids)
        self.cursor, self.offset, self.count = cursor, offset, count
        return True

    def ensure_exists(self) -> None:
        """Create the (possibly empty) file; call when the stage completes."""
        if self._stream is None:
            self._stream = self.path.open("wb")
            self._sync_directory()

    def append(self, records: Iterable[dict[str, Any]]) -> int:
        """Durably append records not already written; return how many were written.

        Records whose source ID was already written are skipped.
        """
        self.ensure_exists()
        assert self._stream is not None
        lines: list[str] = []
        for record in records:
            identifier = record["source"]["id"]
            if identifier in self.seen:
                continue
            self.seen.add(identifier)
            lines.append(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        data = "".join(lines).encode("utf-8")
        self._stream.write(data)
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self.offset += len(data)
        self.count += len(lines)
        return len(lines)

    def _sync_directory(self) -> None:
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def close(self) -> None:
        """Close the file if it was opened."""
        if self._stream is not None:
            self._stream.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()
