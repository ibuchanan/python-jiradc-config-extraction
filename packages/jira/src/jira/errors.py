"""Jira API errors with safe error information."""

from __future__ import annotations


class JiraError(Exception):
    """Exception raised by Jira API operations.

    Attributes:
        status_code: HTTP status code if applicable, None for non-HTTP errors.
        error_type: Exception class name (e.g., "HTTPStatusError", "ValueError").
        stage_status: "unauthorized", "unsupported", or "failed".
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
        stage_status: str = "failed",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type or "JiraError"
        self.stage_status = stage_status

    def __str__(self) -> str:
        """Return error message without response data or credentials."""
        return super().__str__()

    def __repr__(self) -> str:
        """Return safe repr without response data."""
        return (
            f"JiraError({self.args[0]!r}, "
            f"status_code={self.status_code!r}, "
            f"error_type={self.error_type!r}, "
            f"stage_status={self.stage_status!r})"
        )
