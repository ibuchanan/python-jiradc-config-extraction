"""Doctor diagnoses each connection layer without touching a live site."""

from __future__ import annotations

import socket
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx

from cli import main as cli
from cli.doctor import CheckResult, run_checks

if TYPE_CHECKING:
    from collections.abc import Callable

    from _pytest.capture import CaptureFixture
    from _pytest.monkeypatch import MonkeyPatch

PASSWORD = "private-password"  # noqa: S105


def _settings(url: str = "https://jira.example.com/jira") -> Any:  # noqa: ANN401
    return SimpleNamespace(
        url=url,
        user="admin",
        password=SimpleNamespace(get_secret_value=lambda: PASSWORD),
    )


def _resolve(*_args: object, **_kwargs: object) -> list[tuple[Any, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]


def _connect(*_args: object, **_kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(close=lambda: None)


def _transport(auth_status: int = 200, **headers: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/myself"):
            return httpx.Response(auth_status, headers=headers)
        return httpx.Response(401)

    return httpx.MockTransport(handler)


def _run(**overrides: Any) -> list[CheckResult]:  # noqa: ANN401
    options: dict[str, Any] = {
        "resolve": _resolve,
        "connect": _connect,
        "transport": _transport(),
        "environ": {},
    }
    options.update(overrides)
    return run_checks(_settings, **options)


def _by_name(results: list[CheckResult]) -> dict[str, CheckResult]:
    return {result.name: result for result in results}


def test_all_checks_pass() -> None:
    results = _by_name(_run())

    assert {name: r.status for name, r in results.items()} == dict.fromkeys(
        ("config", "url", "dns", "tcp", "http", "auth", "proxy"), "pass"
    )
    assert results["dns"].detail == "10.0.0.5"
    assert PASSWORD not in "".join(r.detail for r in results.values())


def test_config_failure_skips_remaining_checks_without_leaking_secrets() -> None:
    def broken() -> Any:  # noqa: ANN401
        raise ValueError("No jira-dc entry in app-credentials.yaml")

    results = _by_name(run_checks(broken, environ={}))

    assert results["config"] == CheckResult(
        "config", "fail", "No jira-dc entry in app-credentials.yaml"
    )
    assert [results[name].status for name in ("url", "dns", "tcp", "http", "auth")] == [
        "skip"
    ] * 5
    assert results["dns"].detail == "config failed"


def test_dns_failure_reports_os_message_and_skips_network_checks() -> None:
    def no_such_host(*_args: object, **_kwargs: object) -> list[Any]:
        raise socket.gaierror(8, "nodename nor servname provided")

    results = _by_name(_run(resolve=no_such_host))

    assert results["dns"].status == "fail"
    assert "nodename nor servname provided" in results["dns"].detail
    assert results["tcp"] == CheckResult("tcp", "skip", "dns failed")


def test_tls_failure_names_the_cause() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        error = httpx.ConnectError("connect failed")
        error.__cause__ = OSError("[SSL: CERTIFICATE_VERIFY_FAILED] unknown issuer")
        raise error

    results = _by_name(_run(transport=httpx.MockTransport(handler)))

    assert results["http"].status == "fail"
    assert "CERTIFICATE_VERIFY_FAILED" in results["http"].detail
    assert results["auth"].status == "skip"


def test_rejected_credentials_fail_auth_only() -> None:
    results = _by_name(_run(transport=_transport(401)))

    assert results["http"].status == "pass"
    assert results["auth"] == CheckResult(
        "auth", "fail", "credentials rejected (HTTP 401)"
    )


def test_forbidden_reports_safe_login_reason() -> None:
    results = _by_name(
        _run(
            transport=_transport(
                403, **{"X-Seraph-LoginReason": "AUTHENTICATION_DENIED"}
            )
        )
    )

    assert results["auth"].detail == (
        "access forbidden (HTTP 403, login reason AUTHENTICATION_DENIED)"
    )


def test_invalid_url_is_reported_before_any_network_call() -> None:
    def forbidden(*_args: object, **_kwargs: object) -> list[Any]:
        raise AssertionError("must not resolve")

    results = _by_name(
        run_checks(lambda: _settings("jira.example.com"), resolve=forbidden, environ={})
    )

    assert results["url"].status == "fail"
    assert results["dns"].status == "skip"


def test_proxy_variables_warn_without_printing_values() -> None:
    results = _by_name(_run(environ={"HTTPS_PROXY": "http://user:pw@proxy:3128"}))

    assert results["proxy"].status == "warn"
    assert "HTTPS_PROXY" in results["proxy"].detail
    assert "pw" not in results["proxy"].detail


def _doctor_with(
    monkeypatch: MonkeyPatch, results: list[CheckResult]
) -> Callable[[], int]:
    monkeypatch.setattr(cli, "run_checks", lambda _load: results)
    return lambda: cli.main(["doctor"])


def test_doctor_command_exits_nonzero_and_prints_table_on_failure(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    run = _doctor_with(
        monkeypatch,
        [
            CheckResult("config", "pass", "https://jira.example.com as admin"),
            CheckResult("dns", "fail", "gaierror: [Errno 8] no host"),
        ],
    )

    assert run() == 1

    output = capsys.readouterr().out
    assert "dns" in output
    assert "[Errno 8] no host" in output


def test_doctor_command_exits_zero_when_only_warnings(
    monkeypatch: MonkeyPatch,
) -> None:
    run = _doctor_with(monkeypatch, [CheckResult("proxy", "warn", "HTTPS_PROXY set")])

    assert run() == 0
