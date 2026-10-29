# 01: First field export, end to end

**What to build:** An operator can run the CLI against a Jira Data Center site and obtain a first useful JSONL export of custom field definitions, using the existing rate-limited Jira client and a PAT provided through a secure environment variable or non-echoing input.

**Blocked by:** None (can start immediately).

**Status:** completed (commit pending: workspace has no Git metadata)

- [x] The CLI accepts the Jira base URL and output directory and obtains the PAT without requiring it in command arguments.
- [x] The CLI fetches the Jira custom field inventory using a read-only API operation.
- [x] The CLI writes one valid JSON object per JSONL line and reports the number of fields exported.
- [x] HTTP/API errors are surfaced with useful context, without logging the PAT.
- [x] A fixture-driven test covers a successful export and an API failure; no live Jira site is required.

**Update (2026-10-29):** Added `jira-config-export` with `JIRA_PAT` or non-echoing prompt, read-only custom-field export to `fields.jsonl`, and fixture-driven success, failure, and prompt tests. `uv run poe check` passes (8 tests). Git commit is pending because this workspace has no `.git` directory; a repository must be provided or initialized before committing.
