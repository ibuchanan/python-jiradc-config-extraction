"""Tests for loading local Jira Data Center credentials."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from config import load_jira_dc_settings, normalise_url, url_origin

if TYPE_CHECKING:
    from pathlib import Path


def test_loads_only_jira_dc_credentials(tmp_path: Path) -> None:
    path = tmp_path / "app-credentials.yaml"
    path.write_text(
        """other: ignored
apps:
  - name: other
    url: https://other.example.com
  - name: jira-dc
    description: ignored
    url: https://jira.example.com/jira
    user: admin
    password: secret
""",
        encoding="utf-8",
    )

    settings = load_jira_dc_settings(path)

    assert settings.url == "https://jira.example.com/jira"
    assert settings.user == "admin"
    assert settings.password.get_secret_value() == "secret"
    assert "secret" not in repr(settings)


def test_missing_jira_dc_entry(tmp_path: Path) -> None:
    path = tmp_path / "app-credentials.yaml"
    path.write_text("apps: []\n", encoding="utf-8")

    with pytest.raises(ValueError, match="No jira-dc entry"):
        load_jira_dc_settings(path)


def test_missing_or_invalid_fields(tmp_path: Path) -> None:
    path = tmp_path / "app-credentials.yaml"
    path.write_text(
        "apps:\n  - name: jira-dc\n    url: 123\n    user: admin\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as error:
        load_jira_dc_settings(path)

    assert {item["loc"][0] for item in error.value.errors()} == {"url", "password"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" https://jira.example.com/jira/ ", "https://jira.example.com/jira"),
        ("https://jira.example.com", "https://jira.example.com"),
    ],
)
def test_normalise_url_strips_whitespace_and_trailing_slash(
    raw: str, expected: str
) -> None:
    assert normalise_url(raw) == expected


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("jira.example.com", "url must look like"),
        ("ftp://jira.example.com", "url must look like"),
        (
            "https://user:secret@jira.example.com",
            "url must not include a user name or password",
        ),
        ("https://jira.example.com?x=1", "url must not include a query or fragment"),
        ("https://jira.example.com#frag", "url must not include a query or fragment"),
        ("https://jira.example.com:bad", "url has an invalid port"),
    ],
)
def test_normalise_url_rejects(url: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        normalise_url(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://jira.example.com/jira", ("https", "jira.example.com", 443)),
        ("http://jira.example.com", ("http", "jira.example.com", 80)),
        ("https://jira.example.com:8443", ("https", "jira.example.com", 8443)),
    ],
)
def test_url_origin_uses_the_effective_port(
    url: str, expected: tuple[str, str, int]
) -> None:
    assert url_origin(url) == expected
