from __future__ import annotations

import argparse
import os
import io
import inspect
import json
import re
import ssl
import subprocess
import sys
import time
from datetime import datetime, timezone
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib import request


SOURCE_ROOT = Path(__file__).resolve().parents[1]
_runtime_root_value = os.environ.get("BLUEFERN_RUNTIME_ROOT", "").strip()
RUNTIME_ROOT = Path(_runtime_root_value).expanduser().resolve() if _runtime_root_value else SOURCE_ROOT
if _runtime_root_value and not RUNTIME_ROOT.is_dir():
    raise RuntimeError(f"BLUEFERN_RUNTIME_ROOT is not an existing directory: {RUNTIME_ROOT}")
ROOT = RUNTIME_ROOT
SRC = SOURCE_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.bluesky_post import maybe_post_gaza_dispatch_to_bluesky
from bluefern_dispatches.gaza_sources import validate_source_records as validate_collected_source_records
from bluefern_dispatches.operational_health import build_gaza_operational_receipt, write_operational_receipt
from scripts.preflight_repo_state import is_shared_dispatch_record_path
from scripts.test_storage_admission import TEST_ROOT_ENV, admit_test_root
from scripts.run_and_notify import notification_error_message, send_email
from scripts.run_daily_gaza import DEFAULT_PAGES_BRANCH, DEFAULT_REMOTE_URL
import scripts.publish_gaza_historical as publisher
import scripts.run_daily_gaza as daily


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_SOURCE_BRANCH = "add/pages-repo-default"
SAFE_SOURCE_CLEAN_PREFIXES = (
    "output/site/",
    "output/dispatches/",
    "output/review/",
    "output/tmp-backups-pages/",
    "logs/",
)
MANUAL_CORE_FIELDS = (
    "source_record_id",
    "title",
    "url",
    "publisher",
    "published_at",
    "retrieved_at",
    "summary_or_snippet",
    "source_type",
    "region_scope",
    "category_hint",
    "reliability_tier",
)
NORMALIZABLE_FIELDS = {
    "source_record_id",
    "retrieved_at",
    "provider_id",
    "attribution_mode",
    "claim_status",
}
PLACEHOLDER_RECORD_ERROR = "appears to be a placeholder/example source"
PLACEHOLDER_URL_HOSTS = {"example.com", "example.org", "example.net"}
PLACEHOLDER_URL_HINTS = ("placeholder", "todo", "test-source")
PLACEHOLDER_TITLE_HINTS = ("example:", "placeholder", "todo", "test source")
PLACEHOLDER_PUBLISHER_HINTS = ("example news", "placeholder", "test", "test news")
PLACEHOLDER_SUMMARY_HINTS = ("example", "placeholder", "todo", "test source")
PLACEHOLDER_TRACEABILITY_HINTS = (
    "manually added for generator run.",
    "manually added for generator run",
    "generator run",
)
LIVE_RETRY_DELAYS = (0, 15, 30, 60, 60, 60, 60, 60, 60, 60, 60, 60)
LIVE_MARKERS = ("Dispatches From Gaza", "Today's Read", "Today�?Ts Read", "Limited-source update", "Source Mix")


def validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    return value


def manual_source_path(edition_date: str) -> Path:
    return ROOT / "data" / "dispatches" / "gaza" / "sources" / edition_date / "manual_sources.json"


def edition_artifact_dir(edition_date: str) -> Path:
    return ROOT / "output" / "site" / "gaza" / "editions" / edition_date


def audio_file_path(edition_date: str, audio_format: str) -> Path:
    return ROOT / "output" / "site" / "gaza" / "audio" / f"{edition_date}.{audio_format}"


def pages_audio_file_path(pages_repo: Path, edition_date: str, audio_format: str) -> Path:
    return pages_repo / "gaza" / "audio" / f"{edition_date}.{audio_format}"


def _path_is_nonempty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _verify_requested_audio_artifacts(edition_date: str, pages_repo: Path, audio_format: str) -> dict[str, Any]:
    output_audio_path = audio_file_path(edition_date, audio_format)
    pages_audio_path = pages_audio_file_path(pages_repo, edition_date, audio_format)
    missing_paths: list[str] = []
    if not _path_is_nonempty_file(output_audio_path):
        missing_paths.append(str(output_audio_path))
    if not _path_is_nonempty_file(pages_audio_path):
        missing_paths.append(str(pages_audio_path))
    return {
        "ok": not missing_paths,
        "output_audio_path": str(output_audio_path),
        "pages_audio_path": str(pages_audio_path),
        "missing_paths": missing_paths,
        "next_action": (
            "Verify the requested Gaza audio exists and is non-empty at: " + "; ".join(missing_paths)
            if missing_paths
            else None
        ),
    }


def _normalize_repo_path(path: str) -> str:
    return path.replace("\\", "/")


def _run_command(args: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    # In immutable mode, subordinate Python scripts must resolve from the
    # pinned release tree rather than the old scripts under the runtime cwd.
    command = list(args)
    if _runtime_root_value:
        for index, value in enumerate(command):
            if value.endswith(".py") and not Path(value).is_absolute():
                candidate = SOURCE_ROOT / value
                if candidate.is_file():
                    command[index] = str(candidate)
    child_env = os.environ.copy()
    if _runtime_root_value:
        child_env["PYTHONPATH"] = os.pathsep.join(
            [str(SOURCE_ROOT), str(SRC), child_env.get("PYTHONPATH", "")]
        )
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )



# Immutable release overlays execute source code from SOURCE_ROOT while keeping
# data, logs, Pages checkout, credentials, and receipts under RUNTIME_ROOT.
# The launcher validates both roots and sets BLUEFERN_RUNTIME_ROOT explicitly.
if _runtime_root_value:
    daily.ROOT = ROOT
    daily.SRC = SRC
    daily.DEFAULT_PAGES_REPO = ROOT / "bluefern-dispatches-pages"
    publisher.ROOT = ROOT
    publisher.SRC = SRC
    publisher.DEFAULT_PAGES_REPO = daily.DEFAULT_PAGES_REPO
    daily.run_command = _run_command
    publisher.run_command = _run_command
DEFAULT_PAGES_REPO = daily.DEFAULT_PAGES_REPO

def _git_output(repo: Path, *args: str) -> str:
    result = _run_command(["git", *args], cwd=repo)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _git_status_lines(repo: Path) -> list[str]:
    result = _run_command(["git", "status", "--short"], cwd=repo)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git status --short failed")
    return [line for line in result.stdout.splitlines() if line.strip()]

