# 08: Inventory ScriptRunner associations

**What to build:** The export captures directly observable ScriptRunner configuration or behavior associated with fields and screens, and reports gaps when that configuration is not accessible.

**Blocked by:** 02: Safe normalized records and provenance; 03: Resume interrupted extraction safely.

**Status:** ready-for-agent

- [ ] A supported ScriptRunner API is queried read-only for in-scope field/screen configuration and behavior associations.
- [ ] Direct associations reference stable field/screen IDs where available and preserve source evidence and provenance.
- [ ] The extractor does not label behavior as a migration blocker or infer an association solely from names.
- [ ] Unsupported, unavailable, or unauthorized ScriptRunner APIs appear as explicit coverage gaps, not “no configuration found.”
- [ ] Fixture-driven tests distinguish direct association evidence, no matching records, and unavailable/unauthorized source states.
