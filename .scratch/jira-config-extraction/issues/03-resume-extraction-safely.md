# 03: Resume interrupted extraction safely

**What to build:** If an extraction stops partway through, an operator can rerun it against the same output directory, retain completed entity stages, and retry incomplete work without losing data or duplicating records.

**Blocked by:** 02: Safe normalized records and provenance.

**Status:** completed

- [x] A manifest records the export schema version, run identity/timestamps, known Jira/app versions, and source/entity coverage.
- [x] A checkpoint records completed and failed entity-type stages without credentials or secrets.
- [x] A stage is marked complete only after its output has been durably written.
- [x] Rerunning retains completed stages and retries failed/incomplete stages from their beginning.
- [x] Records are deduplicated/upserted using stable source identifiers when a stage is retried.
- [x] A simulated interruption test proves completed data survives, the incomplete stage reruns, and logical records are not duplicated.

**Update (2026-10-29):** The existing fields stage now persists a schema-v1 manifest (run ID, UTC creation time, Jira/app version placeholders, per-stage coverage) and checkpoint (status, count or safe error type/HTTP code). JSONL, checkpoint and manifest use same-directory atomic replacement with file and directory fsync; the checkpoint is marked complete only after durable JSONL installation. Reruns skip completed fields, retry failed/incomplete fields from the beginning, replace rather than append, and deduplicate field records by source ID. Tests simulate interruption after JSONL replacement and during initial checkpoint creation; a failed HTTP stage retries without persisting the PAT, and an already-completed sibling stage survives. `uv run poe check` passes (14 tests). Only the fields stage is wired today; Jira/app versions remain unknown until later source-discovery tickets. Concurrent writers to one output directory are unsupported.
