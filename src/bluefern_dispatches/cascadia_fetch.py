from __future__ import annotations

import os
import platform
import re
import subprocess
import urllib.error
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
        "fallback_used": False,
        "python_fetch_error": None,
        "curl_exit_code": None,
        "curl_stderr_tail": None,
        "tls_or_revocation_hint": None,
        "recommendation": None,
        "bytes_read": 0,
    }


def curl_command(url: str, timeout_seconds: int, user_agent: str, allow_no_revoke: bool) -> list[str]:
    command = ["curl.exe"]
    if allow_no_revoke:
        command.append("--ssl-no-revoke")
    command.extend(["-L", "--max-time", str(timeout_seconds), "-A", user_agent, url])
    return command


def curl_environment() -> dict[str, str]:
    env = dict(os.environ)
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"]:
        env.pop(key, None)
    return env


def run_curl_fetch(url: str, timeout_seconds: int, user_agent: str, diagnostics: dict[str, Any], fallback_used: bool) -> FetchResult:
    allow = curl_no_revoke_allowed()
    diagnostics["fetch_backend"] = "curl" if not fallback_used else "auto"
    diagnostics["fallback_used"] = fallback_used
    if not is_windows():
        diagnostics["tls_or_revocation_hint"] = "curl fallback is only enabled on Windows for this project."
        diagnostics["recommendation"] = diagnostics["tls_or_revocation_hint"]
        return FetchResult(False, "", None, "", diagnostics)
    if not allow:
        diagnostics["tls_or_revocation_hint"] = TLS_OR_REVOCATION_HINT
        diagnostics["recommendation"] = TLS_OR_REVOCATION_HINT
        return FetchResult(False, "", None, "", diagnostics)
    command = curl_command(url, timeout_seconds, user_agent, allow_no_revoke=True)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds + 5, check=False, env=curl_environment())
    except (OSError, subprocess.SubprocessError) as exc:
        diagnostics["curl_exit_code"] = None
        diagnostics["curl_stderr_tail"] = stderr_tail(str(exc))
        diagnostics["recommendation"] = TLS_OR_REVOCATION_HINT
        return FetchResult(False, "", None, "", diagnostics)
    body = completed.stdout or ""
    diagnostics["curl_exit_code"] = completed.returncode
    diagnostics["curl_stderr_tail"] = stderr_tail(completed.stderr or "")
    diagnostics["bytes_read"] = len(body.encode("utf-8"))
    if completed.returncode == 0:
        diagnostics["recommendation"] = CURL_SUCCESS_RECOMMENDATION if fallback_used or diagnostics.get("python_fetch_error") else None
        return FetchResult(True, body, 200, "", diagnostics)
    diagnostics["tls_or_revocation_hint"] = TLS_OR_REVOCATION_HINT
    diagnostics["recommendation"] = TLS_OR_REVOCATION_HINT
    return FetchResult(False, body, None, "", diagnostics)


def fetch_public_url(url: str, timeout_seconds: int, user_agent: str) -> FetchResult:
    backend = fetch_backend()
    diagnostics = base_diagnostics(backend)
    if backend == "curl":
        return run_curl_fetch(url, timeout_seconds, user_agent, diagnostics, fallback_used=False)

    request = urllib.request.Request(url, headers={"User-Agent": user_agent})  # noqa: S310 - curated public URLs only
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            diagnostics["bytes_read"] = len(body.encode("utf-8"))
            return FetchResult(True, body, getattr(response, "status", 200), response.headers.get("Content-Type", ""), diagnostics)
    except urllib.error.HTTPError as exc:
        diagnostics["python_fetch_error"] = f"HTTP Error {exc.code}: {exc.reason}"
        diagnostics["retry_after"] = exc.headers.get("Retry-After") if exc.headers else None
        diagnostics["bytes_read"] = 0
        return FetchResult(False, "", exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", diagnostics)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        diagnostics["python_fetch_error"] = str(exc)
        diagnostics["tls_or_revocation_hint"] = TLS_OR_REVOCATION_HINT if curl_retryable_error(str(exc)) else None
        if backend == "auto" and curl_retryable_error(str(exc)) and is_windows() and curl_no_revoke_allowed():
            return run_curl_fetch(url, timeout_seconds, user_agent, diagnostics, fallback_used=True)
        if backend == "auto" and curl_retryable_error(str(exc)):
            diagnostics["recommendation"] = TLS_OR_REVOCATION_HINT
        return FetchResult(False, "", None, "", diagnostics)
