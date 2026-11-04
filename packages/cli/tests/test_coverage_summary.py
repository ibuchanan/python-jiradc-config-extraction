"""End-to-end coverage reporting at the CLI and artifact seams."""

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from cli import main as cli
from jira import StageStatus

if TYPE_CHECKING:
    from _pytest.capture import CaptureFixture
    from _pytest.monkeypatch import MonkeyPatch
    from conftest import FixtureSetup

_DECLARED_GAP_NAMES = {
    "field_contexts",
    "field_configurations",
    "screen_schemes",
    "issue_type_screen_schemes",
    "elements_connect",
    "dynamic_forms",
    "scriptrunner",
}


def test_cli_summarizes_empty_queried_stages_and_optional_app_gaps(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/serverInfo"):
            return httpx.Response(
                200, json={"version": "11.2.1", "serverTitle": "Jira"}
            )
        if path.endswith("/field"):
            return httpx.Response(200, json=[])
        if path.endswith(("/customFields", "/screens")):
            return httpx.Response(200, json={"values": [], "screens": [], "total": 0})
        raise AssertionError(path)

    jira_basic_auth_fixture("https://jira.example.com", respond)

    assert cli.main(["export", "export"]) == 0
    manifest = json.loads((tmp_path / "export" / "manifest.json").read_text())
    assert manifest["jira_version"] == "11.2.1"
    assert manifest["app_versions"] == {}
    assert manifest["coverage"]["screens"] == {"status": "complete", "count": 0}
    output = capsys.readouterr().out
    assert "Extracting screens..." in output
    for stage, status in (
        ("screens", "complete (0 records, not found)"),
        *((name, "unsupported") for name in _DECLARED_GAP_NAMES),
    ):
        assert re.search(rf"(?m)^{stage}\s+{re.escape(status)}\s*$", output)
    for name in _DECLARED_GAP_NAMES:
        gap = manifest["coverage"][name]
        assert gap["status"] == StageStatus.UNSUPPORTED
        assert gap["reason"]
        assert gap["evidence"]
        assert "not found" not in gap["reason"]
    assert (
        "private-password"
        not in output + (tmp_path / "export" / "manifest.json").read_text()
    )
    assert all(request.method == "GET" for request in requests)


def test_field_failure_reports_not_queried_stages(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path.endswith("/field")
        return httpx.Response(503)

    jira_basic_auth_fixture("https://jira.example.com", respond)
    assert cli.main(["export", "export"]) == 1
    coverage = json.loads((tmp_path / "export" / "manifest.json").read_text())[
        "coverage"
    ]
    assert coverage["fields"]["status"] == "failed"
    assert coverage["screens"] == {
        "status": "not_queried",
        "reason": "fields incomplete",
    }
    assert re.search(r"(?m)^screens\s+not_queried$", capsys.readouterr().out)
    assert len(requests) == 1


def test_unsupported_queried_jira_stage_is_incomplete(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/serverInfo"):
            return httpx.Response(404)
        if path.endswith("/field"):
            return httpx.Response(200, json=[{"id": "customfield_1", "custom": True}])
        if path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "screens": [], "total": 0})
        if path.endswith("/options"):
            return httpx.Response(404)
        if path.endswith("/screens"):
            return httpx.Response(200, json={"values": [], "screens": [], "total": 0})
        raise AssertionError(path)

    jira_basic_auth_fixture("https://jira.example.com", respond)
    assert cli.main(["export", "export"]) == 1
    assert re.search(
        r"(?m)^custom_field_options\s+unsupported\s*$", capsys.readouterr().out
    )
    assert (tmp_path / "export" / "fields.jsonl").exists()


def test_cli_fails_on_required_jira_stage_and_preserves_retryable_data(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    calls = 0
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        nonlocal calls
        path = request.url.path
        if path.endswith("/serverInfo"):
            return httpx.Response(403)
        if path.endswith("/field"):
            return httpx.Response(200, json=[])
        if path.endswith("/customFields"):
            calls += 1
            if calls == 1:
                return httpx.Response(403, json={"error": "Denied"})
            return httpx.Response(200, json={"values": [], "screens": [], "total": 0})
        if path.endswith("/screens"):
            return httpx.Response(200, json={"values": [], "screens": [], "total": 0})
        raise AssertionError(path)

    jira_basic_auth_fixture("https://jira.example.com", respond)
    args = ["export", "export"]
    assert cli.main(args) != 0
    manifest = json.loads((tmp_path / "export" / "manifest.json").read_text())
    assert manifest["jira_version"] is None
    assert manifest["coverage"]["custom_fields"]["status"] == "unauthorized"
    assert (tmp_path / "export" / "fields.jsonl").exists()
    assert re.search(r"(?m)^custom_fields\s+unauthorized\s*$", capsys.readouterr().out)
    assert cli.main(args) == 0
    expected_attempts = 2
    assert calls == expected_attempts
    assert all(request.method == "GET" for request in requests)
    assert "private-password" not in "".join(
        path.read_text() for path in (tmp_path / "export").iterdir() if path.is_file()
    ) + str(capsys.readouterr())


def test_declared_gap_coverage_matches_cli_reference() -> None:
    reference = (
        Path(__file__).parents[3] / "docs" / "reference" / "cli.md"
    ).read_text()
    section = reference.split("## Declared gaps", 1)[1].split("\n## ", 1)[0]
    documented = set(re.findall(r"(?m)^\| `([a-z_]+)` \|", section))
    assert documented == _DECLARED_GAP_NAMES
    assert {gap.name for gap in cli._DECLARED_GAPS} == _DECLARED_GAP_NAMES
    for gap in cli._DECLARED_GAPS:
        assert gap.reason in section
        assert gap.evidence in section
