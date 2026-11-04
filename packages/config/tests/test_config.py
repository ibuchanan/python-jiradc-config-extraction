"""Tests for loading local Jira Data Center credentials."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from config import load_jira_dc_settings

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
