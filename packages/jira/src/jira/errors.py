"""Jira API errors with safe error information."""

from __future__ import annotations

from .status import StageStatus

_MAX_DETAIL_MESSAGE = 200


def _qualified_name(exc: BaseException) -> str:
    """Name builtins plainly and others with their module (``zlib.error``)."""
    cls = type(exc)
    if cls.__module__ == "builtins":
        return cls.__qualname__
    return f"{cls.__module__}.{cls.__qualname__}"


def describe_exception(exc: BaseException) -> str:
    """Summarize an exception's cause chain without response data or credentials.

    Returns class names from outermost to root cause, e.g.
    ``httpx.ConnectError <- ssl.SSLCertVerificationError: [SSL: ...] ...``.
    The root cause's message is included only when it is an ``OSError`` (DNS,
    socket, TLS, and timeout failures), whose text comes from the operating system
    or TLS library. Other messages (HTTP, JSON, validation) may echo remote data,
    so only their class names are reported.
    """
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and all(current is not seen for seen in chain):
        chain.append(current)
        current = current.__cause__ or current.__context__
    summary = " <- ".join(_qualified_name(item) for item in chain)
    root = chain[-1]
    if isinstance(root, OSError) and (message := str(root).strip()):
        summary += f": {message[:_MAX_DETAIL_MESSAGE]}"
    return summary


class JiraError(Exception):
    """Exception raised by Jira API operations.

    Attributes:
        status_code: HTTP status code if applicable, None for non-HTTP errors.
        error_type: Exception class name (e.g., "HTTPStatusError", "ValueError").
        stage_status: The ``StageStatus`` this failure reports.
        detail: Safe cause summary from ``describe_exception``, if available.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
        stage_status: StageStatus | str = StageStatus.FAILED,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type or "JiraError"
        self.stage_status = StageStatus(stage_status)
        self.detail = detail

    def __str__(self) -> str:
        """Return error message without response data or credentials."""
        return super().__str__()

    def __repr__(self) -> str:
        """Return safe repr without response data."""
        return (
            f"JiraError({self.args[0]!r}, "
            f"status_code={self.status_code!r}, "
            f"error_type={self.error_type!r}, "
            f"stage_status={self.stage_status!r}, "
            f"detail={self.detail!r})"
        )
