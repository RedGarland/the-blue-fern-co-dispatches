from __future__ import annotations

import re

CARE_LINE_REVIEW_RE = re.compile(r"^data/dispatches/care-line/review(?:/.*)?$")
CARE_LINE_COLLECTION_RUNS_RE = re.compile(r"^data/dispatches/care-line/collection-runs(?:/.*)?$")
CARE_LINE_LOGS_RE = re.compile(r"^logs/care-line(?:/.*)?$")
CARE_LINE_STATUS_LOCKS_RE = re.compile(r"^status/care-line/locks(?:/.*)?$")
CARE_LINE_STATUS_SCHEDULER_RUNS_RE = re.compile(r"^status/care-line/scheduler-runs(?:/.*)?$")
CARE_LINE_STATUS_FOLLOW_UP_STATE_RE = re.compile(r"^status/care-line/effective-date-follow-up-state\.json$")

CARE_LINE_RUNTIME_CATEGORIES = {
    "logs",
    "local_run_state",
    "review_state",
}

CARE_LINE_ALLOWED_DIRTY_CATEGORIES = set(CARE_LINE_RUNTIME_CATEGORIES)


def normalize_status_path(path_text: str) -> str:
    text = path_text.strip().replace("\\", "/")
    if " -> " in text:
        text = text.split(" -> ", 1)[1].strip()
    if text.startswith("./"):
        return text[2:]
    return text


def classify_care_line_runtime_path(path_text: str) -> str | None:
    path = normalize_status_path(path_text)
    lower = path.lower()
    if not path:
        return None
    if CARE_LINE_REVIEW_RE.match(lower):
        return "review_state"
    if CARE_LINE_COLLECTION_RUNS_RE.match(lower):
        return "local_run_state"
    if CARE_LINE_STATUS_LOCKS_RE.match(lower):
        return "local_run_state"
    if CARE_LINE_STATUS_SCHEDULER_RUNS_RE.match(lower):
        return "local_run_state"
    if CARE_LINE_STATUS_FOLLOW_UP_STATE_RE.match(lower):
        return "local_run_state"
    if CARE_LINE_LOGS_RE.match(lower):
        return "logs"
    return None


def care_line_runtime_paths() -> list[str]:
    return [
        "data/dispatches/care-line/review/",
        "data/dispatches/care-line/collection-runs/",
        "logs/care-line/",
        "status/care-line/locks/",
        "status/care-line/scheduler-runs/",
        "status/care-line/effective-date-follow-up-state.json",
    ]
