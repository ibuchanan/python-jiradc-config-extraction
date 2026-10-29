# 07: Inventory Dynamic Forms associations

**What to build:** The export captures directly observable Dynamic Forms behavior or associations relevant to fields, especially Elements Connect fields, and clearly reports when that data cannot be queried.

**Blocked by:** 02: Safe normalized records and provenance; 03: Resume interrupted extraction safely.

**Status:** blocked — partial implementation; supported read-only Data Center API or approved source required

- [ ] A supported Dynamic Forms API is queried read-only for in-scope configuration/behavior associations. (No supported read-only REST endpoint identified.)
- [ ] Direct associations reference stable field IDs where available and preserve source evidence and provenance. (Pending source API.)
- [x] The extractor does not label fields as migration blockers or infer associations from names alone.
- [ ] Unsupported, unavailable, or unauthorized Dynamic Forms APIs appear as explicit coverage gaps, not “no configuration found.” (Unsupported is reported; unavailable/unauthorized detection requires an API.)
- [ ] Fixture-driven tests distinguish direct association evidence, no matching records, and unavailable/unauthorized source states. (Unsupported coverage tested; remaining cases require an API.)

**Update (2026-10-29):** Vendor [Data Center import/export documentation](https://deviniti.com/support/addon/server/dynamic-forms/latest/import-export/) describes an admin UI JSON export and a Java `ExportConfigurationService.exportAsJsonString()` method, but does not document a read-only REST endpoint for the CLI. The CLI now records `dynamic_forms` as `unsupported` in manifest coverage without claiming zero associations, producing an empty app export, guessing field relationships, or calling an undocumented endpoint. A fixture-driven CLI test verifies coverage, absent association files, GET-only requests, and stable rerun behavior. `uv run poe check` passes (24 tests). **Not complete:** obtain a supported, read-only Data Center configuration API (including version, permissions, response schema, and sample records) or approve a separate input/import mechanism before implementing evidence-based associations and unavailable/unauthorized detection.
