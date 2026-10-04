from __future__ import annotations

import re
from pathlib import Path

FOOD_LINE_DISCOVERY_CANDIDATES_RE = re.compile(
    r"^data/dispatches/food-line/discovery/\d{4}-\d{2}-\d{2}/discovery_candidates\.json$"
)
FOOD_LINE_DISCOVERY_RUN_ROOT_RE = re.compile(r"^data/dispatches/food-line/discovery(?:/.*)?$")
FOOD_LINE_SOURCE_PERFORMANCE_HISTORY_RE = re.compile(r"^status/food-line/runtime/source_performance_history\.json$")
FOOD_LINE_AGENT_INBOX_RE = re.compile(r"^data/dispatches/food-line/agent-inbox(?:/.*)?$")
FOOD_LINE_AGENT_INTAKE_RE = re.compile(r"^data/dispatches/food-line/agent-intake(?:/.*)?$")
FOOD_LINE_REVIEW_RE = re.compile(r"^data/dispatches/food-line/review(?:/.*)?$")
FOOD_LINE_OUTPUT_REVIEW_RE = re.compile(r"^output/review/food-line(?:/.*)?$")
PUBLIC_SITE_VISUAL_REVIEW_RE = re.compile(r"^output/review/public-site-visuals(?:[-_/].*)?$")
BLUEFERN_PUBLIC_SURFACE_REVIEW_RE = re.compile(r"^output/review/bluefern-[a-z0-9_-]+(?:/.*)?$")
GAZA_ARCHIVE_REVIEW_RE = re.compile(r"^output/review/gaza-archive-[a-z0-9_-]+(?:/.*)?$")
PUBLIC_SURFACE_BACKUP_RE = re.compile(r"^output/tmp-backups-pages(?:/.*)?$")
FOOD_LINE_OUTPUT_SITE_RE = re.compile(r"^output/site/food-line(?:/.*)?$")
FOOD_LINE_OUTPUT_DISPATCH_EDITIONS_RE = re.compile(
    r"^output/dispatches/food-line/editions(?:/.*)?$"
)
FOOD_LINE_DISCOVERY_RUNS_RE = re.compile(r"^data/dispatches/food-line/discovery-runs(?:/.*)?$")
FOOD_LINE_DATE_RECONCILIATION_RE = re.compile(
    r"^data/dispatches/food-line/date-reconciliation/\d{4}-\d{2}-\d{2}\.json$"
)
FOOD_LINE_COVERAGE_GAP_RE = re.compile(
    r"^data/dispatches/food-line/coverage-gaps/\d{4}-\d{2}-\d{2}\.json$"
)
FOOD_LINE_HISTORICAL_RECONSTRUCTION_RE = re.compile(
    r"^data/dispatches/food-line/historical-reconstruction/\d{4}-\d{2}-\d{2}/"
    r"(?:reconstruction\.json|research-input\.json|review/candidates\.json|"
    r"review/decisions/food-recon-\d{8}-[a-z0-9-]{1,160}\.json)$"
)
FOOD_LINE_HISTORICAL_RECONSTRUCTION_ROOT_RE = re.compile(
    r"^data/dispatches/food-line/historical-reconstruction(?:/.*)?$"
)
FOOD_LINE_STATUS_RE = re.compile(r"^status/food-line(?:/.*)?$")
FOOD_LINE_OPERATIONAL_HEALTH_RE = re.compile(
    r"^status/operational-health/food-line/\d{4}-\d{2}-\d{2}/"
    r"(?:latest\.json|runs/[A-Za-z0-9_.-]{1,220}\.json)$"
)
FOOD_LINE_OPERATIONAL_RECOVERY_RE = re.compile(
    r"^status/operational-recovery/food-line/\d{4}-\d{2}-\d{2}/"
    r"[A-Za-z0-9_.-]{1,180}/(?:recovery\.lock|latest\.json|attempts/[A-Za-z0-9_.-]{1,220}\.json)$"
)
FOOD_LINE_LOGS_RE = re.compile(r"^logs/food-line(?:/.*)?$")
FOOD_LINE_OPERATIONAL_STATUS_LOGS_RE = re.compile(
    r"^logs/operational-status-exporter(?:/.*)?$"
)
FOOD_LINE_OPERATIONAL_STATUS_WRAPPER_LOGS_RE = re.compile(
    r"^logs/operational-status-exporter-wrapper(?:/.*)?$"
)
FOOD_LINE_AGENT_HISTORY_RE = re.compile(r"^data/agent-history-staging/food-line(?:/.*)?$")
FOOD_LINE_MUTABLE_TRACKED_RUNTIME_PATHS = frozenset()

FOOD_LINE_RUNTIME_CATEGORIES = {
    "generated_public_output",
    "local_run_state",
    "logs",
    "review_output",
}

FOOD_LINE_ALLOWED_DIRTY_CATEGORIES = {
    "cache",
    "local_run_state",
    "logs",
    "review_output",
    "virtualenv",
}


def _normalize_path(path_text: str) -> str:
    text = path_text.strip().replace("\\", "/")
    if " -> " in text:
        text = text.split(" -> ", 1)[1].strip()
    if text.startswith("./"):
        return text[2:]
    return text


def classify_food_line_runtime_path(path_text: str) -> str | None:
    path = _normalize_path(path_text)
    lower = path.lower()
    if not path:
        return None
    if (
        lower.startswith(".pytest_cache/")
        or lower.startswith(".pytest-temp")
        or lower.startswith(".pytest-tmp")
        or lower.startswith(".pytest_tmp")
        or lower.startswith(".tmp")
        or lower.startswith("tmp/")
        or "__pycache__/" in lower
        or "/cache/" in lower
    ):
        return "cache"
    if FOOD_LINE_AGENT_INBOX_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_SOURCE_PERFORMANCE_HISTORY_RE.match(lower):
        return "local_run_state"
    if lower.startswith("status/food-line/runtime/"):
        return "local_run_state"
    if FOOD_LINE_AGENT_INTAKE_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_DISCOVERY_CANDIDATES_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_DISCOVERY_RUN_ROOT_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_DISCOVERY_RUNS_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_DATE_RECONCILIATION_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_COVERAGE_GAP_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_HISTORICAL_RECONSTRUCTION_RE.match(lower):
        return "review_output"
    if FOOD_LINE_HISTORICAL_RECONSTRUCTION_ROOT_RE.match(lower):
        return "unknown"
    if FOOD_LINE_STATUS_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_OPERATIONAL_HEALTH_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_OPERATIONAL_RECOVERY_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_AGENT_HISTORY_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_REVIEW_RE.match(lower):
        return "review_output"
    if PUBLIC_SITE_VISUAL_REVIEW_RE.match(lower):
        return "review_output"
    if BLUEFERN_PUBLIC_SURFACE_REVIEW_RE.match(lower):
        return "review_output"
    if GAZA_ARCHIVE_REVIEW_RE.match(lower):
        return "review_output"
    if FOOD_LINE_OUTPUT_REVIEW_RE.match(lower):
        return "review_output"
    if PUBLIC_SURFACE_BACKUP_RE.match(lower):
        return "review_output"
    if FOOD_LINE_OUTPUT_SITE_RE.match(lower):
        return "generated_public_output"
    if FOOD_LINE_OUTPUT_DISPATCH_EDITIONS_RE.match(lower):
        return "generated_public_output"
    if FOOD_LINE_LOGS_RE.match(lower):
        return "logs"
    if FOOD_LINE_OPERATIONAL_STATUS_LOGS_RE.match(lower):
        return "logs"
    if FOOD_LINE_OPERATIONAL_STATUS_WRAPPER_LOGS_RE.match(lower):
        return "logs"
    return None


def is_food_line_generated_public_output_path(path_text: str) -> bool:
    path = _normalize_path(path_text).lower()
    return bool(FOOD_LINE_OUTPUT_SITE_RE.match(path) or FOOD_LINE_OUTPUT_DISPATCH_EDITIONS_RE.match(path))


def is_food_line_mutable_tracked_runtime_path(path_text: str) -> bool:
    path = _normalize_path(path_text).lower()
    return path in FOOD_LINE_MUTABLE_TRACKED_RUNTIME_PATHS or bool(FOOD_LINE_COVERAGE_GAP_RE.match(path))


def food_line_runtime_paths() -> list[str]:
    return [
        "status/food-line/",
        "status/operational-health/food-line/",
        "status/operational-recovery/food-line/",
        "logs/food-line/",
        "logs/operational-status-exporter/",
        "logs/operational-status-exporter-wrapper/",
        "data/dispatches/food-line/agent-inbox/",
        "data/dispatches/food-line/agent-intake/",
        "data/dispatches/food-line/review/",
        "data/dispatches/food-line/discovery/",
        "data/dispatches/food-line/discovery-runs/",
        "data/dispatches/food-line/date-reconciliation/",
        "data/dispatches/food-line/coverage-gaps/",
        "data/dispatches/food-line/historical-reconstruction/",
        "output/review/food-line/",
        "output/review/public-site-visuals",
        "output/review/bluefern-",
        "output/review/gaza-archive-",
        "output/tmp-backups-pages/",
        "output/site/food-line/",
        "output/dispatches/food-line/editions/",
        "data/agent-history-staging/food-line/",
    ]
