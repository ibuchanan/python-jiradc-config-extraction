# 03: Resume interrupted extraction safely

**What to build:** If an extraction stops partway through, an operator can rerun it against the same output directory, retain completed entity stages, and retry incomplete work without losing data or duplicating records.

**Blocked by:** 02: Safe normalized records and provenance.

**Status:** ready-for-agent

- [ ] A manifest records the export schema version, run identity/timestamps, known Jira/app versions, and source/entity coverage.
- [ ] A checkpoint records completed and failed entity-type stages without credentials or secrets.
- [ ] A stage is marked complete only after its output has been durably written.
- [ ] Rerunning retains completed stages and retries failed/incomplete stages from their beginning.
- [ ] Records are deduplicated/upserted using stable source identifiers when a stage is retried.
- [ ] A simulated interruption test proves completed data survives, the incomplete stage reruns, and logical records are not duplicated.
