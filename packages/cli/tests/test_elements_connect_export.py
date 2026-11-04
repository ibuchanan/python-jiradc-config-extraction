"""Elements Connect evidence and capability reporting at the CLI seam."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from cli import main as cli

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.capture import CaptureFixture
    from _pytest.monkeypatch import MonkeyPatch
    from conftest import FixtureSetup

APP_KEY = "com.valiantys.jira.plugins.SQLFeed"
APP_VERSION = "6.12.0"
FIELD_ID = "customfield_10001"
FIELD_TYPE_KEY = f"{APP_KEY}:nfeed-standard-customfield-type"
SECRET = "secret-token=do-not-log"


def _element_connect_field() -> dict[str, object]:
    return {
        "id": FIELD_ID,
        "custom": True,
        "name": "Linked item",
        "schema": {"custom": FIELD_TYPE_KEY, "type": "string"},
    }


def _plugin() -> dict[str, object]:
    return {
        "key": APP_KEY,
        "name": "Elements Connect",
        "version": APP_VERSION,
        "userInstalled": True,
        "enabled": True,
        "vendor": {"name": "Elements"},
        "license": SECRET,
    }


def test_cli_reports_elements_connect_config_as_a_gap_without_guessing_api(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/serverInfo"):
            return httpx.Response(404)
        if request.url.path.endswith("/field"):
            return httpx.Response(200, json=[_element_connect_field()])
        if request.url.path.endswith(("/customFields", "/screens")):
            return httpx.Response(200, json={"values": [], "screens": [], "total": 0})
        if request.url.path.endswith("/customFields/10001/options"):
            return httpx.Response(200, json={"options": [], "total": 0})
        raise AssertionError(f"Unexpected API request: {request.url.path}")

    jira_basic_auth_fixture("https://jira.example.com", respond)

    args = ["export", "export"]
    assert cli.main(args) == 0
    manifest = json.loads((tmp_path / "export" / "manifest.json").read_text())
    coverage = manifest["coverage"]
    gap = coverage["elements_connect"]
    assert gap["status"] == "unsupported"
    assert gap["reason"] == (
        "no supported read-only Elements Connect DC config API identified"
    )
    assert gap["evidence"]
    assert manifest["app_versions"] == {}
    assert coverage["app_inventory"] == {
        "status": "complete",
        "count": 0,
        "api": "internal",
    }
    assert coverage["associations"] == {
        "status": "complete",
        "count": 0,
        "api": "internal",
    }
    assert not (tmp_path / "export" / "elements-connect.jsonl").exists()
    assert (tmp_path / "export" / "associations.jsonl").read_text() == ""
    assert all(request.method == "GET" for request in requests)

    assert cli.main(args) == 0
    assert (
        json.loads((tmp_path / "export" / "manifest.json").read_text())["coverage"][
            "elements_connect"
        ]
        == gap
    )


def test_cli_links_elements_connect_field_to_detected_app_with_full_raw_objects(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    requests: list[httpx.Request] = []
    plugin = _plugin()

    def respond(request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        requests.append(request)
        path = request.url.path
        if path.endswith("/serverInfo"):
            return httpx.Response(404)
        if path.endswith("/plugins/1.0/"):
            return httpx.Response(
                200,
                json={
                    "plugins": [
                        plugin,
                        {
                            "key": "com.atlassian.jira",
                            "name": "Jira Core",
                            "version": "11.3.12",
                            "userInstalled": False,
                        },
                    ],
                    "total": 2,
                },
            )
        if path.endswith("/field"):
            return httpx.Response(200, json=[_element_connect_field()])
        if path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "total": 0})
        if path.endswith("/customFields/10001/options"):
            return httpx.Response(200, json={"options": [], "total": 0})
        if path.endswith(("/rest/api/2/project", "/rest/api/2/issuetype")):
            return httpx.Response(200, json=[])
        if path.endswith("/rest/api/2/issuetypescheme"):
            return httpx.Response(200, json={"schemes": []})
        if path.endswith("/screens"):
            return httpx.Response(200, json={"screens": [], "total": 0})
        raise AssertionError(f"Unexpected API request: {path}")

    jira_basic_auth_fixture("https://jira.example.com", respond, defaults=False)

    assert cli.main(["export", "export"]) == 0

    export = tmp_path / "export"
    manifest = json.loads((export / "manifest.json").read_text())
    assert manifest["app_versions"] == {APP_KEY: APP_VERSION}
    assert manifest["coverage"]["app_inventory"] == {
        "status": "complete",
        "count": 1,
        "api": "internal",
    }
    assert manifest["coverage"]["associations"] == {
        "status": "complete",
        "count": 1,
        "api": "internal",
    }
    gap = manifest["coverage"]["elements_connect"]
    assert gap["status"] == "unsupported"
    assert gap["reason"] == (
        "no supported read-only Elements Connect DC config API identified"
    )
    assert gap["evidence"]

    app_record = json.loads((export / "app-inventory.jsonl").read_text())
    assert app_record["source"] == {
        "system": "jira",
        "entity": "app",
        "id": APP_KEY,
        "endpoint": "/rest/plugins/1.0/",
    }
    assert datetime.fromisoformat(app_record["extracted_at"]).tzinfo == UTC
    assert app_record["app"] == {
        "key": APP_KEY,
        "name": "Elements Connect",
        "version": APP_VERSION,
        "vendor": "Elements",
        "enabled": True,
        "user_installed": True,
    }
    assert app_record["raw"] == plugin

    association = json.loads((export / "associations.jsonl").read_text())
    assert association["source"] == {
        "system": "jira",
        "entity": "field_app_association",
        "id": f"{FIELD_ID}:{APP_KEY}",
        "endpoint": "/rest/api/2/field",
    }
    assert datetime.fromisoformat(association["extracted_at"]).tzinfo == UTC
    assert association["field_id"] == FIELD_ID
    assert association["app_key"] == APP_KEY
    assert association["app"] == {
        "name": "Elements Connect",
        "version": APP_VERSION,
    }
    assert association["evidence"] == {
        "field_type_key": FIELD_TYPE_KEY,
        "app_key": APP_KEY,
        "app_endpoint": "/rest/plugins/1.0/",
        "match": "field schema.custom prefix equals installed app key",
    }
    assert association["raw"]["field"]["schema"]["custom"] == FIELD_TYPE_KEY
    assert association["raw"]["app"] == plugin

    assert not (export / "elements-connect.jsonl").exists()
    assert SECRET in (export / "app-inventory.jsonl").read_text()
    assert SECRET in (export / "associations.jsonl").read_text()
    for artifact in ("manifest.json", "checkpoint.json"):
        assert SECRET not in (export / artifact).read_text()
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert all(request.method == "GET" for request in requests)


def test_cli_reports_unauthorized_app_inventory_as_advisory(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        requests.append(request)
        path = request.url.path
        if path.endswith("/serverInfo"):
            return httpx.Response(404)
        if path.endswith("/plugins/1.0/"):
            return httpx.Response(403, json={"error": "Denied"})
        if path.endswith("/field"):
            return httpx.Response(200, json=[_element_connect_field()])
        if path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "total": 0})
        if path.endswith("/customFields/10001/options"):
            return httpx.Response(200, json={"options": [], "total": 0})
        if path.endswith(("/rest/api/2/project", "/rest/api/2/issuetype")):
            return httpx.Response(200, json=[])
        if path.endswith("/rest/api/2/issuetypescheme"):
            return httpx.Response(200, json={"schemes": []})
        if path.endswith("/screens"):
            return httpx.Response(200, json={"screens": [], "total": 0})
        raise AssertionError(f"Unexpected API request: {path}")

    jira_basic_auth_fixture("https://jira.example.com", respond, defaults=False)

    assert cli.main(["export", "export"]) == 0

    export = tmp_path / "export"
    manifest = json.loads((export / "manifest.json").read_text())
    assert manifest["app_versions"] == {}
    assert manifest["coverage"]["app_inventory"] == {
        "status": "unauthorized",
        "error": "HTTPStatusError",
        "http_status": 403,
        "api": "internal",
    }
    assert manifest["coverage"]["associations"] == {
        "status": "not_queried",
        "reason": "app_inventory incomplete",
    }
    assert manifest["coverage"]["elements_connect"]["status"] == "unsupported"
    assert not (export / "app-inventory.jsonl").exists()
    assert not (export / "associations.jsonl").exists()

    output = capsys.readouterr().out
    assert re.search(r"(?m)^app_inventory\s+unauthorized\s*$", output)
    assert re.search(r"(?m)^associations\s+not_queried\s*$", output)
    assert all(request.method == "GET" for request in requests)
