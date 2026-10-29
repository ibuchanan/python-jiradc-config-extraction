# jira

Minimal asynchronous Jira Data Center API client and version-pinned OpenAPI spec container for the configuration extraction project.

```python
import asyncio
from jira import JiraClient


async def main() -> None:
    async with JiraClient("https://jira.example.com", token="...") as client:
        response = await client.get("/rest/api/2/field")
        print(response.json())


asyncio.run(main())
```

The client sends the personal access token as a Bearer token, rate-limits requests by default, and retries HTTP 429 responses up to a bounded number of attempts. Keep credentials out of source control; pass them from a secure runtime source.

Export custom field definitions from a Jira Data Center site:

```sh
export JIRA_PAT='your-personal-access-token'
uv run jira-config-export https://jira.example.com/jira ./export
```

Alternatively, omit `JIRA_PAT` to enter the token at a non-echoing prompt. The command writes `./export/fields.jsonl` (one custom field per line), `manifest.json`, and `checkpoint.json`, then prints the field count. It uses only `GET /rest/api/2/field`; system fields are excluded. A failed request exits nonzero and records a safe failure status without replacing an existing field export. Rerun with the same directory to skip a completed field stage or retry an incomplete/failed one. The manifest has schema version, run ID, creation timestamp, unknown Jira/app version placeholders, and stage coverage; the checkpoint has stage status, count or safe error type and HTTP status. App versions are not queried yet.

Each output file is written to a temporary file in the destination directory, flushed and synchronized, then atomically replaced and directory-synchronized. `fields.jsonl` is installed before the checkpoint is marked complete. A crash before that checkpoint leaves the stage eligible for a full retry, which replaces its file and deduplicates by stable field ID (last occurrence wins); a crash after the checkpoint but before manifest coverage is updated is repaired on rerun. Completed sibling stages are retained. Run this on a local filesystem with atomic same-directory rename and directory `fsync` support; concurrent writers to one output directory are not supported.

Each line is a schema version 1 record, for example:

```json
{"extracted_at":"2026-10-29T00:00:00+00:00","field":{"name":"Team"},"schema_version":1,"source":{"endpoint":"/rest/api/2/field","entity":"field","id":"customfield_10001","system":"jira"}}
```

`source.system`, `source.entity`, and `source.id` identify the source record; `source.endpoint` names the read-only API, and `extracted_at` is the UTC time of this export. The exporter sorts fields by Jira ID and JSON keys lexicographically. Only the field name is retained: other source properties (including descriptions and app metadata) are omitted rather than copied verbatim. Names containing credential assignments such as `password=...` are replaced with `[REDACTED]`. This does not guarantee detection of arbitrary secrets embedded in ordinary names; review and protect output files as sensitive configuration data.
