"""Typed credentials and URL validation for local application configuration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import yaml
from pydantic import SecretStr, StrictStr  # noqa: TC002 -- Pydantic resolves fields at runtime
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from urllib.parse import SplitResult

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _parse_url(url: str) -> SplitResult:
    """Parse a URL and validate its scheme, host, and port."""
    parts = urlsplit(url)
    if parts.scheme not in _DEFAULT_PORTS or not parts.hostname:
        raise ValueError("url must look like https://host[:port][/context]")
    try:
        _ = parts.port
    except ValueError as exc:
        raise ValueError("url has an invalid port") from exc
    return parts


def normalise_url(url: str) -> str:
    """Validate a Jira base URL and return it without a trailing slash."""
    candidate = url.strip().rstrip("/")
    parts = _parse_url(candidate)
    if parts.username is not None or parts.password is not None:
        raise ValueError("url must not include a user name or password")
    if parts.query or parts.fragment:
        raise ValueError("url must not include a query or fragment")
    return candidate


def url_origin(url: str) -> tuple[str, str, int]:
    """Return the scheme, host, and effective port that identify a Jira instance."""
    parts = _parse_url(url)
    return (
        parts.scheme,
        parts.hostname or "",
        parts.port or _DEFAULT_PORTS[parts.scheme],
    )


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


__all__ = [
    "JiraDCSettings",
    "load_jira_dc_settings",
    "normalise_url",
    "url_origin",
]
