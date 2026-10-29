# 09: Report coverage and incomplete extraction

**What to build:** At the end of a run, an operator can tell what was extracted, what was not queried or unavailable, and whether the result is complete enough to use; materially incomplete runs return a non-success exit status while preserving successful output for resumption.

**Blocked by:** 04: Extract field contexts and configurations; 05: Extract screens and field placements; 06: Inventory Elements Connect configuration; 07: Inventory Dynamic Forms associations; 08: Inventory ScriptRunner associations.

**Status:** ready-for-agent

- [ ] The CLI displays extraction progress by entity/source stage and a final coverage summary with record counts.
- [ ] The summary distinguishes “not found” from “not queried,” “unsupported,” “unauthorized,” and “failed.”
- [ ] The manifest records known Jira/app versions and per-source/entity coverage status.
- [ ] A materially incomplete run returns a non-success exit status but preserves successful records and checkpoint state for resumption.
- [ ] Tests cover full success, optional app-source gaps, required Jira-stage failure, and summary counts/statuses.
- [ ] No Jira or app mutation endpoint is called by the CLI or extraction path.
