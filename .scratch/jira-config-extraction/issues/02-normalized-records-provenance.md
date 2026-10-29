# 02: Safe normalized records and provenance

**What to build:** Exported configuration records have a versioned, stable shape that downstream analysis can identify and trace back to Jira or an app source without preserving secrets.

**Blocked by:** 01: First field export, end to end.

**Status:** completed

- [x] Each exported field record follows a documented schema version and includes stable source identifiers.
- [x] Records include provenance sufficient to identify source system/entity, source endpoint or category, and extraction time.
- [x] Record serialization is deterministic for equivalent source data (apart from the run timestamp).
- [x] Secret-bearing values are omitted or redacted, with a safe redaction indicator when useful.
- [x] Tests prove a secret-like fixture value does not appear in exported records, errors, or logs.

**Update (2026-10-29):** Export now writes documented schema v1 records containing Jira field IDs, endpoint and UTC extraction time. Fields and JSON keys are sorted; only names are retained, and credential-assignment names are marked `[REDACTED]`. Fixture tests cover normalized records, secret-bearing metadata/name omission, and an API error body containing a secret. `uv run poe check` passes (9 tests). Arbitrary secrets hidden in ordinary names cannot be detected reliably; treat exports as sensitive.
