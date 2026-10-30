# 06: Inventory Elements Connect configuration

**What to build:** The export captures Elements Connect field configuration and direct associations exposed by its supported API, so analysts can inspect the evidence without the tool claiming migration compatibility.

**Blocked by:** 02: Safe normalized records and provenance; 03: Resume interrupted extraction safely; 04: Extract field contexts and configurations.

**Status:** blocked — partial implementation; supported read-only configuration API or approved source required

- [ ] A supported Elements Connect API is queried read-only for in-scope field configuration and metadata.
- [ ] Elements Connect records reference Jira fields by stable IDs when exposed by the source.
- [ ] Direct association evidence is preserved with provenance; no association is inferred solely from a field name.
- [ ] App-specific details needed for analysis are preserved in normalized extension fields without silently dropping observed behavior.
- [ ] Unsupported app versions, unavailable APIs, and authorization failures are recorded as capability/coverage gaps.
- [ ] Fixture-driven tests cover a configured Elements Connect field, a direct field association, secret redaction, and unavailable/unauthorized integration states. (Unsupported coverage is tested; configuration fixtures require an approved source.)

**Update (2026-10-30):** TDD added `elements_connect` as `unsupported` in manifest coverage with a CLI fixture proving no invented app endpoint, empty app export, or inferred association is produced. The vendor [REST API guide](https://doc.elements-apps.com/elements-connect/elements-connect-rest-api) describes generating rendered field HTML, not reading configuration; its [field configuration guide](https://doc.elements-apps.com/elements-connect/field-configuration) documents an admin/browser JSON export, not a supported read-only configuration REST API. `uv run poe check` passes (26 tests). **Not complete:** obtain a supported read-only configuration API/version or approved alternative source before adding app records, direct association evidence, redaction tests, or unauthorized/unavailable integration tests. This gap is not evidence of zero configured fields.
