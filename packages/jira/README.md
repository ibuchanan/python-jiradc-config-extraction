# jira

Minimal asynchronous Jira Data Center API client and version-pinned OpenAPI spec container for the configuration extraction project.

```python
import asyncio
from jira import JiraClient


async def main() -> None:
    async with JiraClient(
        "https://jira.example.com", user="admin", password="..."
    ) as client:
        response = await client.get("/rest/api/2/field")
        print(response.json())


asyncio.run(main())
```

The client uses `user=` and `password=` for HTTP Basic authentication. It rate-limits requests by default and retries HTTP 429 responses up to a bounded number of attempts. Keep credentials out of source control; pass them from a secure runtime source.

For the export command, see [cli](../cli/README.md).
