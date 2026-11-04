"""Typed credentials for local application configuration."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import SecretStr, StrictStr  # noqa: TC002 -- Pydantic resolves fields at runtime
from pydantic_settings import BaseSettings, SettingsConfigDict


class JiraDCSettings(BaseSettings):
    """Connection details for the jira-dc entry in app-credentials.yaml."""

    model_config = SettingsConfigDict(extra="ignore", env_prefix="JIRA_DC_")

    url: StrictStr
    user: StrictStr
    password: SecretStr


def load_jira_dc_settings(
    path: Path = Path(".scratch/app-credentials.yaml"),
) -> JiraDCSettings:
    """Read only the Jira Data Center connection details from the local YAML file."""
    with path.open(encoding="utf-8") as source:
        document = yaml.safe_load(source)

    apps = document.get("apps") if isinstance(document, dict) else None
    if not isinstance(apps, list):
        raise ValueError("Expected an apps list in app-credentials.yaml")

    app = next(
        (
            item
            for item in apps
            if isinstance(item, dict) and item.get("name") == "jira-dc"
        ),
        None,
    )
    if app is None:
        raise ValueError("No jira-dc entry in app-credentials.yaml")

    fields = ("url", "user", "password")
    return JiraDCSettings(**{key: app[key] for key in fields if key in app})


__all__ = ["JiraDCSettings", "load_jira_dc_settings"]
