from __future__ import annotations

import re

CARE_LINE_REVIEW_RE = re.compile(r"^data/dispatches/care-line/review(?:/.*)?$")
CARE_LINE_COLLECTION_RUNS_RE = re.compile(r"^data/dispatches/care-line/collection-runs(?:/.*)?$")
CARE_LINE_QUEUE_RUNS_RE = re.compile(r"^data/dispatches/care-line/queue-runs(?:/.*)?$")
CARE_LINE_SOURCES_RE = re.compile(r"^data/dispatches/care-line/sources(?:/.*)?$")
CARE_LINE_INCIDENTS_RE = re.compile(r"^data/dispatches/incidents(?:/.*)?$")
CARE_LINE_LOGS_RE = re.compile(r"^logs/care-line(?:/.*)?$")
CARE_LINE_STATUS_LOCKS_RE = re.compile(r"^status/care-line/locks(?:/.*)?$")
CARE_LINE_STATUS_SCHEDULER_RUNS_RE = re.compile(r"^status/care-line/scheduler-runs(?:/.*)?$")
CARE_LINE_STATUS_PUBLICATION_SCHEDULER_RUNS_RE = re.compile(r"^status/care-line/publication-scheduler-runs(?:/.*)?$")
CARE_LINE_STATUS_FOLLOW_UP_STATE_RE = re.compile(r"^status/care-line/effective-date-follow-up-state\.json$")
CARE_LINE_OPERATOR_RUN_EVIDENCE_RE = re.compile(
    r"^ops/operator/runs/\d{4}-\d{2}-\d{2}/[A-Za-z0-9][A-Za-z0-9_.-]{0,239}(?:/[A-Za-z0-9][A-Za-z0-9_.-]{0,239})*$"
)
CARE_LINE_GENERATED_PUBLIC_OUTPUT_RE = re.compile(r"^output/(?:site|dispatches)(?:/.*)?$")

CARE_LINE_RUNTIME_CATEGORIES = {
    "logs",
    "local_run_state",
    "review_state",
}

CARE_LINE_ALLOWED_DIRTY_CATEGORIES = set(CARE_LINE_RUNTIME_CATEGORIES)
CARE_LINE_GENERATED_PUBLIC_OUTPUT_RESIDUE_STATUSES = {" M", " D", "??"}


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
    if CARE_LINE_QUEUE_RUNS_RE.match(lower):
        return "local_run_state"
    if CARE_LINE_SOURCES_RE.match(lower):
        return "local_run_state"
    if CARE_LINE_INCIDENTS_RE.match(lower):
        return "local_run_state"
    if CARE_LINE_STATUS_LOCKS_RE.match(lower):
        return "local_run_state"
    if CARE_LINE_STATUS_SCHEDULER_RUNS_RE.match(lower):
        return "local_run_state"
    if CARE_LINE_STATUS_PUBLICATION_SCHEDULER_RUNS_RE.match(lower):
        return "local_run_state"
    if CARE_LINE_STATUS_FOLLOW_UP_STATE_RE.match(lower):
        return "local_run_state"
    if CARE_LINE_OPERATOR_RUN_EVIDENCE_RE.match(lower):
        return "local_run_state"
    if CARE_LINE_LOGS_RE.match(lower):
        return "logs"
    return None


def is_care_line_generated_public_output_path(path_text: str) -> bool:
    path = normalize_status_path(path_text).lower()
    return bool(CARE_LINE_GENERATED_PUBLIC_OUTPUT_RE.match(path))


def care_line_runtime_paths() -> list[str]:
    return [
        "data/dispatches/care-line/review/",
        "data/dispatches/care-line/collection-runs/",
        "data/dispatches/care-line/queue-runs/",
        "data/dispatches/care-line/sources/",
        "data/dispatches/incidents/",
        "logs/care-line/",
        "status/care-line/locks/",
        "status/care-line/scheduler-runs/",
        "status/care-line/publication-scheduler-runs/",
        "status/care-line/effective-date-follow-up-state.json",
        "ops/operator/runs/",
    ]
