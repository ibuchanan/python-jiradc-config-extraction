"""The coverage status vocabulary shared by checkpoint, manifest, and summary."""

from enum import StrEnum


class StageStatus(StrEnum):
    """How a queried stage or a declared source ended.

    ``COMPLETE`` is a successful query, including a query that returned no
    records. ``NOT_QUERIED`` means a prerequisite stage did not complete.
    ``UNSUPPORTED`` means no supported read route is known, or Jira answered
    404, 405, or 501. ``UNAUTHORIZED`` means Jira answered 401 or 403.
    ``FAILED`` means the request or response failed.
    """

    COMPLETE = "complete"
    NOT_QUERIED = "not_queried"
    UNSUPPORTED = "unsupported"
    UNAUTHORIZED = "unauthorized"
    FAILED = "failed"
