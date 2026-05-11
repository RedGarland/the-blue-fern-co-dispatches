from __future__ import annotations

import os
import platform
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_FETCH_BACKEND = "auto"
DEFAULT_ALLOW_CURL_NO_REVOKE = "0"
TLS_OR_REVOCATION_HINT = "Set CASCADIA_ALLOW_CURL_NO_REVOKE=1 only if you accept the local Windows revocation-check workaround, or fix Windows certificate revocation/proxy/security settings."
CURL_SUCCESS_RECOMMENDATION = "Windows revocation checks appear to block default fetches; curl fallback succeeded because CASCADIA_ALLOW_CURL_NO_REVOKE=1."


@dataclass
class FetchResult:
    ok: bool
    body: str
    status_code: int | None
    content_type: str
    diagnostics: dict[str, Any]


def fetch_backend() -> str:
    value = os.environ.get("CASCADIA_FETCH_BACKEND", DEFAULT_FETCH_BACKEND).strip().lower()
    return value if value in {"auto", "python", "curl"} else DEFAULT_FETCH_BACKEND


def curl_no_revoke_allowed() -> bool:
    return os.environ.get("CASCADIA_ALLOW_CURL_NO_REVOKE", DEFAULT_ALLOW_CURL_NO_REVOKE).strip() == "1"


def is_windows() -> bool:
    return os.name == "nt" or platform.system().lower() == "windows"


def curl_retryable_error(message: str) -> bool:
    lowered = message.lower()
    needles = [
        "connection refused",
        "actively refused",
        "ssl",
        "tls",
        "certificate",
        "cert",
        "revocation",
        "remote end closed",
        "remote close",
        "connection reset",
    ]
    return any(needle in lowered for needle in needles)


def stderr_tail(value: str, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text[-limit:]


def base_diagnostics(backend: str) -> dict[str, Any]:
    return {
        "fetch_backend": backend,
        "selected_backend": backend,
        "fallback_used": False,
        "python_fetch_error": None,
        "curl_exit_code": None,
        "curl_stderr_tail": None,
        "tls_or_revocation_hint": None,
        "recommendation": None,
        "bytes_read": 0,
        "dns_ok": None,
        "head_status": None,
        "get_status": None,
        "status_code": None,
        "content_type": "",
        "failure_reason": None,
        "exception_class": None,
        "elapsed_ms": 0,
        "retry_count": 0,
    }


def curl_command(url: str, timeout_seconds: int, user_agent: str, allow_no_revoke: bool) -> list[str]:
    command = ["curl.exe"]
    if allow_no_revoke:
        command.append("--ssl-no-revoke")
    command.extend(["-L", "--max-time", str(timeout_seconds), "--connect-timeout", str(max(3, min(10, timeout_seconds))), "-A", user_agent, "-i", url])
    return command


def curl_environment() -> dict[str, str]:
    env = dict(os.environ)
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"]:
        env.pop(key, None)
    return env


def run_curl_fetch(url: str, timeout_seconds: int, user_agent: str, diagnostics: dict[str, Any], fallback_used: bool) -> FetchResult:
    allow = curl_no_revoke_allowed()
    diagnostics["fetch_backend"] = "curl" if not fallback_used else "auto"
    if not is_windows():
        diagnostics["tls_or_revocation_hint"] = "curl fallback is only enabled on Windows for this project."
        diagnostics["recommendation"] = diagnostics["tls_or_revocation_hint"]
        return FetchResult(False, "", None, "", diagnostics)
    if not allow:
        diagnostics["fallback_used"] = False
        diagnostics["tls_or_revocation_hint"] = TLS_OR_REVOCATION_HINT
        diagnostics["recommendation"] = TLS_OR_REVOCATION_HINT
        diagnostics["failure_reason"] = "tls_revocation"
        return FetchResult(False, "", None, "", diagnostics)
    diagnostics["fallback_used"] = fallback_used
    command = curl_command(url, timeout_seconds, user_agent, allow_no_revoke=True)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds + 5, check=False, env=curl_environment())
    except (OSError, subprocess.SubprocessError) as exc:
        diagnostics["curl_exit_code"] = None
        diagnostics["curl_stderr_tail"] = stderr_tail(str(exc))
        diagnostics["exception_class"] = type(exc).__name__
        diagnostics["recommendation"] = TLS_OR_REVOCATION_HINT
        diagnostics["failure_reason"] = categorize_failure(str(exc), None)
        return FetchResult(False, "", None, "", diagnostics)
    full_output = completed.stdout or ""
    diagnostics["curl_exit_code"] = completed.returncode
    diagnostics["curl_stderr_tail"] = stderr_tail(completed.stderr or "")
    body = full_output
    if "\r\n\r\n" in full_output:
        body = full_output.split("\r\n\r\n", 1)[1]
    elif "\n\n" in full_output:
        body = full_output.split("\n\n", 1)[1]
    diagnostics["bytes_read"] = len(body.encode("utf-8"))
    if completed.returncode == 0:
        diagnostics["selected_backend"] = "curl"
        diagnostics["failure_reason"] = None
        diagnostics["recommendation"] = CURL_SUCCESS_RECOMMENDATION if fallback_used or diagnostics.get("python_fetch_error") else None
        return FetchResult(True, body, 200, "", diagnostics)
    diagnostics["tls_or_revocation_hint"] = TLS_OR_REVOCATION_HINT
    diagnostics["recommendation"] = TLS_OR_REVOCATION_HINT
    diagnostics["failure_reason"] = categorize_failure(completed.stderr or "", None)
    return FetchResult(False, body, None, "", diagnostics)


def dns_check(url: str) -> bool:
    try:
        host = urllib.parse.urlsplit(url).hostname  # type: ignore[attr-defined]
        if not host:
            return False
        socket.getaddrinfo(host, None)
        return True
    except OSError:
        return False


def categorize_failure(message: str, status_code: int | None) -> str:
    lowered = (message or "").lower()
    if status_code == 403:
        return "http_403"
    if status_code == 404:
        return "http_404"
    if status_code and status_code >= 500:
        return "http_5xx"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "getaddrinfo" in lowered or "name resolution" in lowered or "nodename" in lowered or "dns" in lowered:
        return "dns_failure"
    if "revocation" in lowered or "schannel" in lowered:
        return "tls_revocation"
    if "certificate" in lowered or "ssl" in lowered or "tls" in lowered:
        return "tls_certificate"
    if "403" in lowered:
        return "http_403"
    if "404" in lowered:
        return "http_404"
    if "429" in lowered:
        return "blocked"
    return "unknown_error"


def _single_python_request(url: str, timeout_seconds: int, user_agent: str, method: str) -> FetchResult:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent}, method=method)  # noqa: S310 - curated public URLs only
    diagnostics = base_diagnostics("python")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace") if method != "HEAD" else ""
            diagnostics["bytes_read"] = len(body.encode("utf-8"))
            diagnostics["status_code"] = getattr(response, "status", 200)
            diagnostics["content_type"] = response.headers.get("Content-Type", "")
            return FetchResult(True, body, diagnostics["status_code"], diagnostics["content_type"], diagnostics)
    except urllib.error.HTTPError as exc:
        diagnostics["python_fetch_error"] = f"HTTP Error {exc.code}: {exc.reason}"
        diagnostics["status_code"] = exc.code
        diagnostics["failure_reason"] = categorize_failure(diagnostics["python_fetch_error"], exc.code)
        diagnostics["retry_after"] = exc.headers.get("Retry-After") if exc.headers else None
        return FetchResult(False, "", exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", diagnostics)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        diagnostics["python_fetch_error"] = str(exc)
        diagnostics["exception_class"] = type(exc).__name__
        diagnostics["failure_reason"] = categorize_failure(str(exc), None)
        diagnostics["tls_or_revocation_hint"] = TLS_OR_REVOCATION_HINT if curl_retryable_error(str(exc)) else None
        return FetchResult(False, "", None, "", diagnostics)


def fetch_public_url(url: str, timeout_seconds: int, user_agent: str) -> FetchResult:
    started = time.monotonic()
    backend = fetch_backend()
    diagnostics = base_diagnostics(backend)
    diagnostics["dns_ok"] = dns_check(url)
    if diagnostics["dns_ok"] is False:
        diagnostics["failure_reason"] = "dns_failure"
    if backend == "curl":
        result = run_curl_fetch(url, timeout_seconds, user_agent, diagnostics, fallback_used=False)
        result.diagnostics["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return result

    max_attempts = 3
    last_result: FetchResult | None = None
    for attempt in range(max_attempts):
        diagnostics["retry_count"] = attempt
        get = _single_python_request(url, timeout_seconds, user_agent, "GET")
        diagnostics["get_status"] = get.status_code
        diagnostics["status_code"] = get.status_code
        diagnostics["content_type"] = get.content_type
        diagnostics["bytes_read"] = get.diagnostics.get("bytes_read", 0)
        diagnostics["python_fetch_error"] = get.diagnostics.get("python_fetch_error")
        diagnostics["exception_class"] = get.diagnostics.get("exception_class")
        diagnostics["failure_reason"] = get.diagnostics.get("failure_reason")
        diagnostics["retry_after"] = get.diagnostics.get("retry_after")
        if get.ok and (get.body or get.content_type):
            diagnostics["selected_backend"] = "python"
            diagnostics["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            return FetchResult(True, get.body, get.status_code, get.content_type, diagnostics)
        last_result = get
        if get.status_code is not None and int(get.status_code) >= 400:
            break

    error_text = str(diagnostics.get("python_fetch_error") or "")
    if backend == "auto" and is_windows() and (curl_retryable_error(error_text) or diagnostics.get("failure_reason") in {"dns_failure", "timeout", "tls_certificate", "tls_revocation"}):
        curl_result = run_curl_fetch(url, timeout_seconds, user_agent, diagnostics, fallback_used=True)
        curl_result.diagnostics["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return curl_result
    if backend == "auto" and curl_retryable_error(error_text):
        diagnostics["recommendation"] = TLS_OR_REVOCATION_HINT
    diagnostics["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return FetchResult(False, "", last_result.status_code if last_result else None, "", diagnostics)
