from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.cascadia_fetch import fetch_public_url  # noqa: E402
from bluefern_dispatches.cascadia_source_registry import load_source_registry  # noqa: E402


USER_AGENT = "BlueFernDispatches/0.1 windows-fetch-diagnostics"


def dns_ok(url: str) -> bool:
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname
    if not host:
        return False
    try:
        socket.getaddrinfo(host, None)
        return True
    except OSError:
        return False


def run_mode(url: str, mode: str, timeout_seconds: int) -> dict[str, object]:
    started = time.monotonic()
    os.environ["CASCADIA_FETCH_BACKEND"] = "auto" if mode.startswith("fetch_public_url_auto") else ("python" if "python" in mode else "curl")
    os.environ["CASCADIA_ALLOW_CURL_NO_REVOKE"] = "1" if mode.endswith("no_revoke") else "0"
    result = fetch_public_url(url, timeout_seconds, USER_AGENT)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "backend_attempted": mode,
        "dns_ok": dns_ok(url),
        "head_status": result.diagnostics.get("head_status"),
        "get_status": result.diagnostics.get("get_status"),
        "final_success": bool(result.ok),
        "content_length": len((result.body or "").encode("utf-8")),
        "content_type": result.content_type,
        "failure_reason": result.diagnostics.get("failure_reason"),
        "exception_class": result.diagnostics.get("exception_class"),
        "elapsed_ms": elapsed_ms,
        "retry_count": result.diagnostics.get("retry_count", 0),
        "selected_backend": result.diagnostics.get("selected_backend"),
    }


def run_curl_direct(url: str, timeout_seconds: int, no_revoke: bool) -> dict[str, object]:
    command = ["curl.exe", "-L", "--max-time", str(timeout_seconds), "--connect-timeout", "8", "-A", USER_AGENT, "-I" if False else "-i", url]
    if no_revoke:
        command.insert(1, "--ssl-no-revoke")
    started = time.monotonic()
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    success = completed.returncode == 0
    return {
        "backend_attempted": "curl_direct_no_revoke" if no_revoke else "curl_direct",
        "dns_ok": dns_ok(url),
        "head_status": None,
        "get_status": 200 if success else None,
        "final_success": success,
        "content_length": len((completed.stdout or "").encode("utf-8")),
        "content_type": "",
        "failure_reason": None if success else "unknown_error",
        "exception_class": None,
        "elapsed_ms": elapsed_ms,
        "retry_count": 0,
        "selected_backend": "curl",
    }


def pick_sources(registry: list[dict[str, object]]) -> list[dict[str, object]]:
    preferred_ids = {
        "wa-governor-news",
        "or-governor-news",
        "id-governor-news",
        "wa-dot-news",
        "or-dot-news",
        "id-transit-news",
        "wa-ecology-news",
        "or-deq-news",
        "id-deq-news",
        "opb-news-feed",
    }
    chosen = [item for item in registry if str(item.get("source_id")) in preferred_ids]
    if len(chosen) >= 8:
        return chosen[:10]
    fetchable = [item for item in registry if item.get("enabled") and item.get("source_type") in {"rss", "atom", "official_page", "press_release_page", "alert_feed"}]
    return fetchable[:10]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=12)
    args = parser.parse_args()
    registry = load_source_registry(ROOT)
    chosen = pick_sources(registry)
    modes = [
        "fetch_public_url_python",
        "fetch_public_url_auto",
        "fetch_public_url_auto_no_revoke",
        "curl_direct",
        "curl_direct_no_revoke",
    ]
    diagnostics: list[dict[str, object]] = []
    for source in chosen:
        source_id = str(source.get("source_id") or "")
        url = str(source.get("url") or "")
        group = str(source.get("source_type") or "unknown")
        for mode in modes:
            row = run_curl_direct(url, args.timeout, mode.endswith("no_revoke")) if mode.startswith("curl_direct") else run_mode(url, mode, args.timeout)
            diagnostics.append(
                {
                    "url": url,
                    "source_id": source_id,
                    "source_group": group,
                    **row,
                }
            )
    out = ROOT / "output" / "dispatches" / "cascadia" / "fetch_diagnostics" / "windows_fetch_diagnostics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": time.time(), "results": diagnostics}, indent=2), encoding="utf-8")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
