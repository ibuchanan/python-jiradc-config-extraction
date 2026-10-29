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

Alternatively, omit `JIRA_PAT` to enter the token at a non-echoing prompt. The command writes `./export/fields.jsonl` (one custom field per line) and prints the field count. It uses only `GET /rest/api/2/field`; system fields are excluded. A failed request exits nonzero without writing a new export.
