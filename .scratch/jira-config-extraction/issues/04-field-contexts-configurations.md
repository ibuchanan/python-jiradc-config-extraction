# 04: Extract field contexts and configurations

**What to build:** The export captures the Jira configuration that governs where custom fields apply and how they are configured, including contexts, options, field configurations, and available scheme associations.

**Blocked by:** 03: Resume interrupted extraction safely.

**Status:** ready-for-agent

- [ ] Field contexts and their project/issue-type applicability are extracted where exposed by the supported Jira APIs.
- [ ] Context options are extracted where applicable and available.
- [ ] Field configurations and their associations to relevant schemes are exported where available.
- [ ] Relationships reference stable Jira IDs and retain source provenance.
- [ ] Fixture-driven tests cover representative contexts, options, configurations, missing/empty results, and paginated results where the API uses pagination.
- [ ] Unsupported or unauthorized endpoints are reported as coverage gaps rather than misrepresented as empty configuration.
