from __future__ import annotations

import re
from pathlib import Path

FOOD_LINE_DISCOVERY_CANDIDATES_RE = re.compile(
    r"^data/dispatches/food-line/discovery/\d{4}-\d{2}-\d{2}/discovery_candidates\.json$"
)
FOOD_LINE_DISCOVERY_RUN_ROOT_RE = re.compile(r"^data/dispatches/food-line/discovery(?:/.*)?$")
FOOD_LINE_SOURCE_PERFORMANCE_HISTORY_RE = re.compile(r"^data/dispatches/food-line/source_performance_history\.json$")
FOOD_LINE_AGENT_INBOX_RE = re.compile(r"^data/dispatches/food-line/agent-inbox(?:/.*)?$")
FOOD_LINE_AGENT_INTAKE_RE = re.compile(r"^data/dispatches/food-line/agent-intake(?:/.*)?$")
FOOD_LINE_REVIEW_RE = re.compile(r"^data/dispatches/food-line/review(?:/.*)?$")
FOOD_LINE_OUTPUT_REVIEW_RE = re.compile(r"^output/review/food-line(?:/.*)?$")
FOOD_LINE_DISCOVERY_RUNS_RE = re.compile(r"^data/dispatches/food-line/discovery-runs(?:/.*)?$")
FOOD_LINE_STATUS_RE = re.compile(r"^status/food-line(?:/.*)?$")
FOOD_LINE_LOGS_RE = re.compile(r"^logs/food-line(?:/.*)?$")
FOOD_LINE_AGENT_HISTORY_RE = re.compile(r"^data/agent-history-staging/food-line(?:/.*)?$")

FOOD_LINE_RUNTIME_CATEGORIES = {
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
    if FOOD_LINE_AGENT_INBOX_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_SOURCE_PERFORMANCE_HISTORY_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_AGENT_INTAKE_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_DISCOVERY_CANDIDATES_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_DISCOVERY_RUN_ROOT_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_DISCOVERY_RUNS_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_STATUS_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_AGENT_HISTORY_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_REVIEW_RE.match(lower):
        return "review_output"
    if FOOD_LINE_OUTPUT_REVIEW_RE.match(lower):
        return "review_output"
    if FOOD_LINE_LOGS_RE.match(lower):
        return "logs"
    return None


def food_line_runtime_paths() -> list[str]:
    return [
        "status/food-line/",
        "logs/food-line/",
        "data/dispatches/food-line/agent-inbox/",
        "data/dispatches/food-line/agent-intake/",
        "data/dispatches/food-line/review/",
        "data/dispatches/food-line/discovery/",
        "data/dispatches/food-line/discovery-runs/",
        "output/review/food-line/",
        "data/agent-history-staging/food-line/",
    ]
