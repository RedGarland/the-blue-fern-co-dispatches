"""Isolated one-query worker for bounded Food Line discovery runs."""
from __future__ import annotations

import argparse
import json
import os
import ssl
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .food_line_discovery_expansion import (
    run_food_line_discovery_expansion,
    set_food_line_request_timeout,
)


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class RetryingNetworkFetcher:
    """urllib fetcher with a strict per-attempt timeout and bounded retries."""

    def __init__(self, *, timeout_seconds: int, max_retries: int, backoff_seconds: float = 0.25) -> None:
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.max_retries = max(0, int(max_retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))

    def __call__(self, url: str, timeout: int = 15) -> bytes:
        del timeout
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "application/rss+xml;q=0.8,*/*;q=0.7",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        final_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                    return response.read(2_000_000)
            except urllib.error.URLError as exc:
                final_error = exc
                reason = getattr(exc, "reason", None)
                if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
                    try:
                        with urllib.request.urlopen(
                            request,
                            timeout=self.timeout_seconds,
                            context=ssl._create_unverified_context(),
                        ) as response:  # noqa: S310
                            return response.read(2_000_000)
                    except Exception as insecure_exc:  # noqa: BLE001
                        final_error = insecure_exc
            except Exception as exc:  # noqa: BLE001
                final_error = exc
            if attempt < self.max_retries and self.backoff_seconds:
                time.sleep(min(self.backoff_seconds * (2**attempt), 2.0))
        assert final_error is not None
        raise final_error


def execute_worker(payload: dict[str, Any]) -> dict[str, Any]:
    root = Path(str(payload["root"])).resolve()
    options = dict(payload.get("options") or {})
    query = dict(payload["query"])
    timeout_seconds = max(1, int(options.get("per_request_timeout_seconds") or 15))
    set_food_line_request_timeout(timeout_seconds)
    fetcher = RetryingNetworkFetcher(
        timeout_seconds=timeout_seconds,
        max_retries=max(0, int(options.get("max_retries") or 0)),
    )
    result = run_food_line_discovery_expansion(
        root,
        str(payload["edition_date"]),
        fetcher=fetcher,
        max_results_per_query=max(1, int(options.get("max_results_per_query") or 3)),
        query_lookback_days=max(0, int(options.get("query_lookback_days") or 1)),
        query_lookahead_days=max(0, int(options.get("query_lookahead_days") or 1)),
        public_claim_lookback_days=max(0, int(options.get("public_claim_lookback_days") or 0)),
        public_claim_lookahead_days=max(0, int(options.get("public_claim_lookahead_days") or 0)),
        dry_run=True,
        export_agent_inbox=False,
        query_plan_override=[query],
        include_candidate_records=True,
    )
    query_rows = list(result.get("query_rows") or [])
    query_error = ""
    if query_rows:
        query_error = str(query_rows[0].get("query_error") or query_rows[0].get("direct_fetch_error") or "").strip()
    return {
        "schema_version": "food_line_bounded_query_result_v1",
        "query_id": str(payload["query_id"]),
        "status": "failed" if query_error else "completed",
        "error": query_error,
        "query_rows": query_rows,
        "candidates": list(result.pop("_candidate_records", []) or []),
        "summary": {
            "candidate_count": int(result.get("candidate_count") or 0),
            "public_eligible_candidate_count": int(result.get("public_eligible_candidate_count") or 0),
            "blocked_fetch_count": int(result.get("blocked_fetch_count") or 0),
            "duplicate_count": int(result.get("duplicate_count") or 0),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one isolated bounded Food Line query.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        result = execute_worker(payload)
    except Exception as exc:  # noqa: BLE001
        result = {
            "schema_version": "food_line_bounded_query_result_v1",
            "query_id": "",
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "query_rows": [],
            "candidates": [],
            "summary": {},
        }
    _atomic_write_json(output_path, result)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
