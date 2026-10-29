# 06: Inventory Elements Connect configuration

**What to build:** The export captures Elements Connect field configuration and direct associations exposed by its supported API, so analysts can inspect the evidence without the tool claiming migration compatibility.

**Blocked by:** 02: Safe normalized records and provenance; 03: Resume interrupted extraction safely; 04: Extract field contexts and configurations.

**Status:** ready-for-agent

- [ ] A supported Elements Connect API is queried read-only for in-scope field configuration and metadata.
- [ ] Elements Connect records reference Jira fields by stable IDs when exposed by the source.
- [ ] Direct association evidence is preserved with provenance; no association is inferred solely from a field name.
- [ ] App-specific details needed for analysis are preserved in normalized extension fields without silently dropping observed behavior.
- [ ] Unsupported app versions, unavailable APIs, and authorization failures are recorded as capability/coverage gaps.
- [ ] Fixture-driven tests cover a configured Elements Connect field, a direct field association, secret redaction, and unavailable/unauthorized integration states.
