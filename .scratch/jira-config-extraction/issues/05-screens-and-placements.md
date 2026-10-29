# 05: Extract screens and field placements

**What to build:** The export shows which fields are placed on which Jira screens and tabs, and how screens are selected through screen schemes and issue-type screen schemes.

**Blocked by:** 03: Resume interrupted extraction safely.

**Status:** ready-for-agent

- [ ] Screens and their tabs are exported with stable Jira identifiers.
- [ ] Field placements on each tab are exported with their order/position where the API exposes it.
- [ ] Screen schemes and issue-type screen scheme mappings are exported where exposed by the supported APIs.
- [ ] Relationships preserve source IDs and provenance rather than relying on names alone.
- [ ] Fixture-driven tests cover representative screen/tab/placement and scheme relationships.
- [ ] Unsupported, unauthorized, or failed screen endpoints are distinguished from a successful empty response.
