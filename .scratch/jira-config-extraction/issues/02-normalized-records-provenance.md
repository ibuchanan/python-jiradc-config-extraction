# 02: Safe normalized records and provenance

**What to build:** Exported configuration records have a versioned, stable shape that downstream analysis can identify and trace back to Jira or an app source without preserving secrets.

**Blocked by:** 01: First field export, end to end.

**Status:** ready-for-agent

- [ ] Each exported field record follows a documented schema version and includes stable source identifiers.
- [ ] Records include provenance sufficient to identify source system/entity, source endpoint or category, and extraction time.
- [ ] Record serialization is deterministic for equivalent source data.
- [ ] Secret-bearing values are omitted or redacted, with a safe redaction indicator when useful.
- [ ] Tests prove a secret-like fixture value does not appear in exported records, errors, or logs.
