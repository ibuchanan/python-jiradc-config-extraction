# 07: Inventory Dynamic Forms associations

**What to build:** The export captures directly observable Dynamic Forms behavior or associations relevant to fields, especially Elements Connect fields, and clearly reports when that data cannot be queried.

**Blocked by:** 02: Safe normalized records and provenance; 03: Resume interrupted extraction safely.

**Status:** ready-for-agent

- [ ] A supported Dynamic Forms API is queried read-only for in-scope configuration/behavior associations.
- [ ] Direct associations reference stable field IDs where available and preserve source evidence and provenance.
- [ ] The extractor does not label fields as migration blockers or infer associations from names alone.
- [ ] Unsupported, unavailable, or unauthorized Dynamic Forms APIs appear as explicit coverage gaps, not “no configuration found.”
- [ ] Fixture-driven tests distinguish direct association evidence, no matching records, and unavailable/unauthorized source states.
