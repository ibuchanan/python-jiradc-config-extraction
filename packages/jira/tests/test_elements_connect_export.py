"""Elements Connect capability reporting at the public CLI seam."""

import json
from typing import TYPE_CHECKING

import httpx

from jira import cli

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch


def test_cli_reports_elements_connect_unsupported_without_guessing_api(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    client = cli.JiraClient

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/serverInfo"):
            return httpx.Response(404)
        if request.url.path.endswith("/field"):
            return httpx.Response(
                200,
                json=[
                    {"id": "customfield_10001", "custom": True, "name": "Linked item"}
                ],
            )
        if request.url.path.endswith(("/customFields", "/screens")):
            return httpx.Response(200, json={"values": [], "total": 0})
        if request.url.path.endswith("/customFields/10001/options"):
            return httpx.Response(200, json={"options": [], "total": 0})
        raise AssertionError(f"Unexpected API request: {request.url.path}")

    monkeypatch.setattr(
        cli,
        "JiraClient",
        lambda url, token: client(
            url,
            token,
            requests_per_minute=60_000,
            transport=httpx.MockTransport(respond),
        ),
    )
    monkeypatch.setenv("JIRA_PAT", "test-pat")

    args = ["https://jira.example.com", str(tmp_path)]
    assert cli.main(args) == 0
    coverage = json.loads((tmp_path / "manifest.json").read_text())["coverage"]
    assert coverage["elements_connect"] == {
        "status": "unsupported",
        "reason": "no supported read-only Elements Connect DC config API identified",
    }
    assert not (tmp_path / "elements-connect.jsonl").exists()
    assert not (tmp_path / "associations.jsonl").exists()
    assert all(request.method == "GET" for request in requests)

    assert cli.main(args) == 0
    assert (
        json.loads((tmp_path / "manifest.json").read_text())["coverage"][
            "elements_connect"
        ]
        == coverage["elements_connect"]
    )
