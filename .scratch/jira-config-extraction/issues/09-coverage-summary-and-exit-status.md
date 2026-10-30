# 09: Report coverage and incomplete extraction

**What to build:** At the end of a run, an operator can tell what was extracted, what was not queried or unavailable, and whether the result is complete enough to use; materially incomplete runs return a non-success exit status while preserving successful output for resumption.

**Blocked by:** 04: Extract field contexts and configurations; 05: Extract screens and field placements; 06: Inventory Elements Connect configuration; 07: Inventory Dynamic Forms associations; 08: Inventory ScriptRunner associations.

**Status:** completed (with documented source limitations)

- [x] The CLI displays extraction progress by entity/source stage and a final coverage summary with record counts.
- [x] The summary distinguishes “not found” from “not queried,” “unsupported,” “unauthorized,” and “failed.”
- [x] The manifest records known Jira/app versions and per-source/entity coverage status. (Jira version is queried when accessible; app versions stay unknown without an approved source.)
- [x] A materially incomplete run returns a non-success exit status but preserves successful records and checkpoint state for resumption.
- [x] Tests cover full success, optional app-source gaps, required Jira-stage failure, and summary counts/statuses.
- [x] No Jira or app mutation endpoint is called by the CLI or extraction path.

**Update (2026-10-30):** TDD added a stage-progress display, final manifest-backed coverage summary, optional read-only Jira `/serverInfo` version lookup, and nonzero exit for any incomplete queried Jira stage; static unsupported APIs remain explicitly reported but advisory. An early field-inventory failure marks not-yet-queried stages as such without overwriting existing sibling coverage. Successful files/checkpoints survive and failed stages retry on rerun. Fixture tests cover full queried-stage success with static app gaps, empty results, unauthorized and unsupported Jira stages, field failure, version lookup failure, and retry; `uv run poe check` passes (30 tests). No app-version API has been identified, so `app_versions` remains empty rather than inventing values.
