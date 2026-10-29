# 04: Extract field contexts and configurations

**What to build:** The export captures the Jira configuration that governs where custom fields apply and how they are configured, including contexts, options, field configurations, and available scheme associations.

**Blocked by:** 03: Resume interrupted extraction safely.

**Status:** blocked — partial implementation; explicit context/configuration API required

- [ ] Field contexts and their project/issue-type applicability are extracted where exposed by the supported Jira APIs. (Field-level applicability exported; explicit context IDs unavailable in bundled API.)
- [x] Context options are extracted where applicable and available. (Options across contexts; source does not expose context association.)
- [ ] Field configurations and their associations to relevant schemes are exported where available. (No supported endpoint in bundled Jira DC 11.2.1 OpenAPI.)
- [x] Relationships reference stable Jira IDs and retain source provenance for supported records.
- [ ] Fixture-driven tests cover representative contexts, options, configurations, missing/empty results, and paginated results where the API uses pagination. (Applicability/options covered; explicit contexts/configurations unavailable.)
- [x] Unsupported or unauthorized endpoints are reported as coverage gaps rather than misrepresented as empty configuration.

**Update (2026-10-29):** TDD added `field-contexts.jsonl` (field-level project/issue-type applicability) and `field-options.jsonl` from the documented read-only `/customFields` and `/customFields/{id}/options` APIs, with pagination, stable IDs, provenance, stage checkpoint/retry, and explicit unsupported/unauthorized/failed coverage. The bundled Jira DC 11.2.1 spec has no explicit field-context, field-configuration, or configuration-scheme read endpoints; these are recorded as unsupported rather than fabricated. `uv run poe check` passes (19 tests). **Not complete:** validate a supported endpoint/version or approved alternative source for explicit contexts/configurations and add those stages; coverage/exit policy is ticket 09.
