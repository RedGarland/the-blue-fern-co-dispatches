"""Portable Care regression tests for the versioned connection-reset patch.

The normal repository run imports the package by name.  The private task-local
run extracts only the reviewed candidate functions from the copied source, so
it does not require or name a production checkout.  Provider access is always
mocked and all persistence is under pytest's temporary directory.
"""
from __future__ import annotations

import ast
import re
import ssl
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest


CANDIDATE = Path(__file__).resolve().parents[1] / "src" / "bluefern_dispatches" / "care_line_national_pipeline.py"


class ProxyError(Exception):
    """Synthetic proxy failure without importing a transport library."""


class _RetryExhausted(RuntimeError):
    def __init__(self, message: str, *, first_failure_reason: str, retry_count: int):
        super().__init__(message)
        self.first_failure_reason = first_failure_reason
        self.retry_count = retry_count


def _load_reviewed_functions() -> dict[str, object]:
    tree = ast.parse(CANDIDATE.read_text(encoding="utf-8"), filename=str(CANDIDATE))
    wanted = {
        "_exception_failure_reason",
        "_source_failure_class",
        "_source_failure_http_status",
        "_source_has_revalidated_external_access",
        "_is_revalidated_external_403",
        "_source_failure_transient",
        "_source_failure_status_class",
        "_is_transient_source_exception",
        "_fetch_source_with_transient_retry",
    }
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace: dict[str, object] = {
        "re": re,
        "urlparse": __import__("urllib.parse", fromlist=["urlparse"]).urlparse,
        "Any": object,
        "CareLineSource": object,
        "SOURCE_FAILURE_CLASSES": {
            "HTTPError",
            "ValueError",
            "ParseError",
            "TimeoutError",
            "URLError",
            "ConnectionResetError",
        },
        "SourceFetchRetryExhausted": _RetryExhausted,
        "fetch_source": None,
    }
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")]), *nodes],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    exec(compile(module, str(CANDIDATE), "exec"), namespace)
    return namespace


@pytest.fixture()
def reviewed():
    return _load_reviewed_functions()


def test_direct_reset_is_transient_but_403_is_not(reviewed):
    reset = "ConnectionResetError: [WinError 10054] connection reset"
    forbidden = "HTTPError: HTTP Error 403: Forbidden"
    assert reviewed["_source_failure_class"](reset) == "ConnectionResetError"
    assert reviewed["_source_failure_transient"](reset, "ConnectionResetError") is True
    assert reviewed["_source_failure_status_class"](reset, "ConnectionResetError") == "transient_network_failure"
    assert reviewed["_source_failure_transient"](forbidden, "HTTPError") is False
    assert reviewed["_source_failure_status_class"](forbidden, "HTTPError") == "external_access_restriction"


def test_generic_connection_ssl_and_proxy_are_not_transient(reviewed):
    for error in (ConnectionError("generic"), ssl.SSLError("TLS"), ProxyError("proxy")):
        reason = reviewed["_exception_failure_reason"](error)
        failure_class = reviewed["_source_failure_class"](reason)
        assert failure_class == "Unknown"
        assert reviewed["_source_failure_transient"](reason, failure_class) is False


def test_urllib_reset_retries_once_and_records_success(reviewed):
    calls: list[int] = []

    def fake_fetch(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise URLError(ConnectionResetError(10054, "reset"))
        return b"payload", {"http_status": 200}

    reviewed["fetch_source"] = fake_fetch
    payload, meta = reviewed["_fetch_source_with_transient_retry"](
        object(), timeout=1, allow_insecure_tls=False
    )
    assert payload == b"payload"
    assert len(calls) == 2
    assert meta["retry_state"] == "retried_success"
    assert meta["retry_count"] == 1
    assert meta["first_failure_reason"].startswith("URLError:")


def test_reset_exhaustion_is_failed_and_bounded(reviewed):
    calls: list[int] = []

    def fake_fetch(*_args, **_kwargs):
        calls.append(1)
        raise ConnectionResetError(10054, "reset")

    reviewed["fetch_source"] = fake_fetch
    with pytest.raises(_RetryExhausted) as caught:
        reviewed["_fetch_source_with_transient_retry"](
            object(), timeout=1, allow_insecure_tls=False
        )
    assert len(calls) == 2
    assert caught.value.retry_count == 1
    assert caught.value.first_failure_reason.startswith("ConnectionResetError:")


def test_403_is_not_retried(reviewed):
    calls: list[int] = []

    def fake_fetch(*_args, **_kwargs):
        calls.append(1)
        raise HTTPError("https://example.invalid/feed", 403, "Forbidden", {}, None)

    reviewed["fetch_source"] = fake_fetch
    with pytest.raises(HTTPError):
        reviewed["_fetch_source_with_transient_retry"](
            object(), timeout=1, allow_insecure_tls=False
        )
    assert calls == [1]


def test_candidate_checkout_fixture_is_clean_only_after_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "add/pages-repo-default"], cwd=repo, check=True, capture_output=True)
    source = repo / "src" / "candidate.py"
    source.parent.mkdir()
    source.write_text("candidate = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=pytest", "-c", "user.email=pytest@example.invalid", "commit", "-m", "candidate"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    clean = subprocess.run(["git", "status", "--porcelain"], cwd=repo, text=True, capture_output=True, check=True)
    assert clean.stdout == ""
    source.write_text("candidate = False\n", encoding="utf-8")
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repo, text=True, capture_output=True, check=True)
    assert dirty.stdout.startswith(" M ")
