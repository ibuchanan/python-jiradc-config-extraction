"""Diagnose configuration and connectivity to Jira Data Center.

Each check builds on the previous one, so the first failure skips the rest and
names the layer that broke: config, URL, DNS, TCP, HTTP/TLS, or authentication.
Detail text never includes the password or response bodies.
"""

from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import httpx

from config import url_origin
from jira import describe_exception

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from config import JiraDCSettings

Status = Literal["pass", "warn", "fail", "skip"]

TIMEOUT_SECONDS = 10.0
PROXY_VARIABLES = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
)
_LOGIN_REASON = re.compile(r"[A-Z_]{1,40}")
_HTTP_OK = 200
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one diagnostic step."""

    name: str
    status: Status
    detail: str


class _CheckFailedError(Exception):
    """A check failed with a message that is already safe to display."""


def _describe(exc: Exception) -> str:
    """Describe a failure; plain ValueErrors are loader messages without secrets."""
    if type(exc) is ValueError:
        return str(exc)
    return describe_exception(exc)


def _check_config(
    load_settings: Callable[[], JiraDCSettings], ctx: dict[str, Any]
) -> str:
    try:
        settings = load_settings()
    except (OSError, ValueError) as exc:
        raise _CheckFailedError(_describe(exc)) from exc
    ctx["base_url"] = settings.url.rstrip("/")
    ctx["auth"] = httpx.BasicAuth(settings.user, settings.password.get_secret_value())
    return f"{settings.url} as {settings.user}"


def _check_url(ctx: dict[str, Any]) -> str:
    try:
        scheme, host, port = url_origin(ctx["base_url"])
    except ValueError as exc:
        raise _CheckFailedError(str(exc)) from exc
    ctx["host"], ctx["port"] = host, port
    return f"{scheme} {host}:{port}"


def _check_dns(resolve: Callable[..., Any], ctx: dict[str, Any]) -> str:
    infos = resolve(ctx["host"], ctx["port"], type=socket.SOCK_STREAM)
    return ", ".join(sorted({info[4][0] for info in infos}))


def _check_tcp(connect: Callable[..., Any], ctx: dict[str, Any]) -> str:
    connect((ctx["host"], ctx["port"]), timeout=TIMEOUT_SECONDS).close()
    return f"connected to {ctx['host']}:{ctx['port']}"


def _get(
    ctx: dict[str, Any],
    path: str,
    transport: httpx.BaseTransport | None,
    auth: httpx.Auth | None = None,
) -> httpx.Response:
    # An explicit transport mirrors the exporter: httpx then ignores proxy variables.
    with httpx.Client(
        transport=transport or httpx.HTTPTransport(), timeout=TIMEOUT_SECONDS
    ) as client:
        try:
            return client.get(ctx["base_url"] + path, auth=auth)
        except httpx.RequestError as exc:
            raise _CheckFailedError(describe_exception(exc)) from exc


def _check_http(ctx: dict[str, Any], transport: httpx.BaseTransport | None) -> str:
    response = _get(ctx, "/rest/api/2/serverInfo", transport)
    return f"HTTP {response.status_code} (TLS and routing OK)"


def _check_auth(ctx: dict[str, Any], transport: httpx.BaseTransport | None) -> str:
    response = _get(ctx, "/rest/api/2/myself", transport, ctx["auth"])
    status = response.status_code
    if status == _HTTP_OK:
        return "credentials accepted (HTTP 200)"
    if status == _HTTP_UNAUTHORIZED:
        raise _CheckFailedError("credentials rejected (HTTP 401)")
    if status == _HTTP_FORBIDDEN:
        reason = response.headers.get("X-Seraph-LoginReason", "")
        suffix = f", login reason {reason}" if _LOGIN_REASON.fullmatch(reason) else ""
        raise _CheckFailedError(f"access forbidden (HTTP 403{suffix})")
    raise _CheckFailedError(f"unexpected HTTP {status}")


def _check_proxy(environ: Mapping[str, str]) -> CheckResult:
    names = [name for name in PROXY_VARIABLES if environ.get(name)]
    if not names:
        return CheckResult("proxy", "pass", "no proxy variables set")
    return CheckResult(
        "proxy",
        "warn",
        f"{', '.join(names)} set, but the exporter ignores proxy variables "
        "(curl would use them)",
    )


def run_checks(  # noqa: PLR0913
    load_settings: Callable[[], JiraDCSettings],
    *,
    resolve: Callable[..., Any] = socket.getaddrinfo,
    connect: Callable[..., Any] = socket.create_connection,
    transport: httpx.BaseTransport | None = None,
    environ: Mapping[str, str] = os.environ,
) -> list[CheckResult]:
    """Run the diagnostics in dependency order and return one result per step."""
    ctx: dict[str, Any] = {}
    steps: list[tuple[str, Callable[[], str]]] = [
        ("config", lambda: _check_config(load_settings, ctx)),
        ("url", lambda: _check_url(ctx)),
        ("dns", lambda: _check_dns(resolve, ctx)),
        ("tcp", lambda: _check_tcp(connect, ctx)),
        ("http", lambda: _check_http(ctx, transport)),
        ("auth", lambda: _check_auth(ctx, transport)),
    ]
    results: list[CheckResult] = []
    failed: str | None = None
    for name, action in steps:
        if failed is not None:
            results.append(CheckResult(name, "skip", f"{failed} failed"))
            continue
        try:
            results.append(CheckResult(name, "pass", action()))
        except _CheckFailedError as exc:
            results.append(CheckResult(name, "fail", str(exc)))
            failed = name
        except OSError as exc:  # DNS and TCP errors carry OS-provided messages
            results.append(CheckResult(name, "fail", describe_exception(exc)))
            failed = name
    results.append(_check_proxy(environ))
    return results
