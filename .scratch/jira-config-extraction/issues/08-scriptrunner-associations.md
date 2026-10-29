# 08: Inventory ScriptRunner associations

**What to build:** The export captures directly observable ScriptRunner configuration or behavior associated with fields and screens, and reports gaps when that configuration is not accessible.

**Blocked by:** 02: Safe normalized records and provenance; 03: Resume interrupted extraction safely.

**Status:** blocked — partial implementation; supported read-only Data Center API or approved source required

- [ ] A supported ScriptRunner API is queried read-only for in-scope field/screen configuration and behavior associations.
- [ ] Direct associations reference stable field/screen IDs where available and preserve source evidence and provenance.
- [x] The extractor does not label behavior as a migration blocker or infer an association solely from names.
- [ ] Unsupported, unavailable, or unauthorized ScriptRunner APIs appear as explicit coverage gaps, not “no configuration found.” (Unsupported is reported; unavailable/unauthorized detection needs an API.)
- [ ] Fixture-driven tests distinguish direct association evidence, no matching records, and unavailable/unauthorized source states. (Pending source API; unsupported coverage tested.)

**Update (2026-10-29):** Adaptavist's [Script Registry documentation](https://docs.adaptavist.com/sr4js/latest/features/script-registry/#exporting-your-scripts) describes an admin/browser ZIP export of scripts, field IDs, and configurations for versions 8.54+, 9.19+, and 10.0+. Its [Configuration Exporter](https://docs.adaptavist.com/sr4js/latest/features/built-in-scripts/configuration-exporter/) is a built-in script invoked with Run to produce YAML, not a documented read-only REST API. No supported read-only configuration endpoint or response schema was identified. TDD added explicit `scriptrunner: unsupported` manifest coverage without emitting an empty app or association file, guessing endpoints, or inferring field/screen associations. The CLI fixture checks GET-only requests and stable reruns. `uv run poe check` passes (25 tests). **Not complete:** obtain a supported read-only Data Center API (versions, permissions, schema, sample records) or approve a separate input/import mechanism before implementing direct associations and unavailable/unauthorized detection.
