# 05: Extract screens and field placements

**What to build:** The export shows which fields are placed on which Jira screens and tabs, and how screens are selected through screen schemes and issue-type screen schemes.

**Blocked by:** 03: Resume interrupted extraction safely.

**Status:** blocked — partial implementation; supported screen-scheme read API required

- [x] Screens and their tabs are exported with stable Jira identifiers.
- [x] Field placements on each tab are exported with their order/position where the API exposes it.
- [ ] Screen schemes and issue-type screen scheme mappings are exported where exposed by the supported APIs. (Neither has a read endpoint in the bundled Jira DC 11.2.1 spec.)
- [x] Relationships preserve source IDs and provenance rather than relying on names alone.
- [ ] Fixture-driven tests cover representative screen/tab/placement and scheme relationships. (Screens/tabs/placements covered; scheme relationships require a supported API.)
- [x] Unsupported, unauthorized, or failed screen endpoints are distinguished from a successful empty response.

**Update (2026-10-29):** TDD added checkpointed `screens.jsonl`, `screen-tabs.jsonl`, and `screen-placements.jsonl` using documented read-only Jira DC APIs. Records retain stable screen/tab/field IDs, source endpoints, extraction timestamps, and zero-based tab/field order. Paginated screen inventory, successful empty results, unauthorized tabs, failed placement retry and deduplication are fixture-tested; dependent stages are `not_queried` when prerequisites fail. Screen schemes and issue-type screen schemes are recorded as unsupported in manifest coverage because the bundled spec contains no read API for them. `uv run poe check` passes (23 tests). Scheme mappings remain blocked on a supported API; ticket 09 exit-status policy is still pending.
