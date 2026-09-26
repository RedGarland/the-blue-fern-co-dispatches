from __future__ import annotations

import re


OPERATOR_LATEST_RE = re.compile(r"^ops/operator/latest\.json$")
OPERATOR_NOTIFICATION_LATEST_RE = re.compile(r"^ops/operator/notification-latest\.json$")
OPERATOR_HISTORY_RE = re.compile(r"^ops/operator/history\.jsonl$")
OPERATOR_INCIDENT_RE = re.compile(r"^ops/operator/incidents/[A-Za-z0-9_.-]{1,220}\.json$")
OPERATOR_RUN_RE = re.compile(
    r"^ops/operator/runs/\d{4}-\d{2}-\d{2}/[A-Za-z0-9_.-]{1,240}\.json$"
)
OPERATOR_REMEDIATION_RE = re.compile(r"^ops/operator/remediation(?:/.*)?$")
OPERATOR_ENGINEERING_RE = re.compile(r"^ops/operator/engineering(?:/.*)?$")
OPERATOR_LOCK_RE = re.compile(r"^ops/operator/\.lock(?:/.*)?$")

OPERATOR_RUNTIME_CATEGORIES = {"local_run_state"}
OPERATOR_ALLOWED_DIRTY_CATEGORIES = set(OPERATOR_RUNTIME_CATEGORIES)


def _normalize_path(path_text: str) -> str:
    text = path_text.strip().replace("\\", "/")
    if " -> " in text:
        text = text.split(" -> ", 1)[1].strip()
    if text.startswith("./"):
        return text[2:]
    return text


def classify_operator_runtime_path(path_text: str) -> str | None:
    path = _normalize_path(path_text)
    lower = path.lower()
    if not path:
        return None
    for pattern in (
        OPERATOR_LATEST_RE,
        OPERATOR_NOTIFICATION_LATEST_RE,
        OPERATOR_HISTORY_RE,
        OPERATOR_INCIDENT_RE,
        OPERATOR_RUN_RE,
        OPERATOR_REMEDIATION_RE,
        OPERATOR_ENGINEERING_RE,
        OPERATOR_LOCK_RE,
    ):
        if pattern.match(lower):
            return "local_run_state"
    return None


def is_operator_mutable_tracked_runtime_path(path_text: str) -> bool:
    path = _normalize_path(path_text).lower()
    return bool(
        OPERATOR_LATEST_RE.match(path)
        or OPERATOR_HISTORY_RE.match(path)
        or OPERATOR_INCIDENT_RE.match(path)
    )


def operator_runtime_paths() -> list[str]:
    return [
        "ops/operator/latest.json",
        "ops/operator/notification-latest.json",
        "ops/operator/history.jsonl",
        "ops/operator/incidents/",
        "ops/operator/runs/",
        "ops/operator/remediation/",
        "ops/operator/engineering/",
        "ops/operator/.lock/",
    ]
