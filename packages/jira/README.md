# jira

Asynchronous Jira Data Center API client backed by the bundled 11.2.1 OpenAPI definition and `openapi-httpx-client` operation methods.

```python
import asyncio
from jira import JiraClient


async def main() -> None:
    async with JiraClient(
        "https://jira.example.com", user="admin", password="..."
    ) as client:
        response = await client.call("getFields")
        print(response.json())


asyncio.run(main())
```

The client uses `user=` and `password=` for HTTP Basic authentication. It rate-limits requests by default and retries HTTP 429 responses up to a bounded number of attempts. Keep credentials out of source control; pass them from a secure runtime source.

The client declares two routes that are not in the bundled OpenAPI document: the internal project-settings route used for view screens, and the UPM route `/rest/plugins/1.0/` used only to read installed app versions. Coverage marks both `"api": "internal"`.

For the export command, see [cli](../cli/README.md).
