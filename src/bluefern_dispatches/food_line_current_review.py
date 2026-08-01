from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit


QUEUE_SCHEMA_VERSION = "food_line_current_signal_review_v1"
PROPOSED_EDITION_SCHEMA_VERSION = "food_line_proposed_edition_v1"
CURRENT_PRODUCTION_SCOPE = "current_nonhistorical_only"
ALLOWED_DECISIONS = ("approve", "approve_with_edit", "hold", "reject")
ALLOWED_EDITORIAL_STATUSES = ("pending_editorial_review", *ALLOWED_DECISIONS)
HISTORICAL_ROOTS = ("data/agent-history", "data/agent-history-staging")
CURRENT_SOURCE_PREFIXES = (
    "data/dispatches/food-line/agent-inbox/",
    "data/dispatches/food-line/agent-intake/",
    "data/dispatches/food-line/discovery/",
    "data/dispatches/food-line/sources/",
    "output/review/food-line/",
)
PRIVATE_QUEUE_PATH = Path("data/dispatches/food-line/review/current-signal-review.json")
PRIVATE_PROPOSED_EDITION_ROOT = Path("data/dispatches/food-line/review/proposed-editions")

REQUIRED_ITEM_FIELDS = (
    "review_item_id",
    "source_finding_or_intake_id",
    "source_artifact_path",
    "source_url",
    "canonical_source_url",
    "publisher",
    "source_published_at",
    "title",
    "exact_supporting_passage",
    "proposed_public_headline",
    "proposed_public_summary",
    "location_name",
    "state",
    "location_scope",
    "pressure_type",
    "affected_groups",
    "why_it_matters",
    "evidence_level",
    "confidence",
    "uncertainty_note",
    "duplicate_check",
    "freshness_check",
    "proposed_section",
    "proposed_rank",
    "editorial_status",
    "editorial_note",
    "publication_eligible",
)
PRIVATE_PAYLOAD_KEYS = {
    "raw_agent_payload",
    "private_text_provenance",
    "chain_of_custody",
    "hidden_instructions",
}
CURRENT_FRESHNESS_WINDOW_DAYS = 3


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def payload_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def write_json_atomic(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = canonical_json(payload)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return path


def _require_date(value: Any, field: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"{field} must contain a valid ISO date") from exc
    if not text.startswith(parsed.isoformat()):
        raise ValueError(f"{field} must begin with a zero-padded ISO date")
    return text


def _require_https(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text.startswith("https://") or not urlsplit(text).netloc:
        raise ValueError(f"{field} must be a canonical HTTPS URL")
    return text


def _article_url_identity(value: str) -> tuple[str, str]:
    parsed = urlsplit(value)
    return parsed.netloc.lower(), parsed.path.rstrip("/") or "/"


def _validate_source_artifact_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or PurePosixPath(text).is_absolute() or ".." in PurePosixPath(text).parts:
        raise ValueError("source_artifact_path must be a repository-relative path")
    lowered = text.lower().rstrip("/")
    if any(lowered == root or lowered.startswith(f"{root}/") for root in HISTORICAL_ROOTS):
        raise ValueError("historical source artifacts cannot enter the current Food Line review queue")
    if not any(lowered.startswith(prefix) for prefix in CURRENT_SOURCE_PREFIXES):
        raise ValueError("source_artifact_path is outside the allowed current Food Line production inputs")
    return text


def _find_private_payload_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = PRIVATE_PAYLOAD_KEYS.intersection(value)
        for child in value.values():
            found.update(_find_private_payload_keys(child))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for child in value:
            found.update(_find_private_payload_keys(child))
        return found
    return set()


def _validate_item(item: Any, index: int, *, edition_date: date) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"items[{index}] must be an object")
    missing = [field for field in REQUIRED_ITEM_FIELDS if field not in item]
    if missing:
        raise ValueError(f"items[{index}] is missing required fields: {', '.join(missing)}")
    forbidden = sorted(_find_private_payload_keys(item))
    if forbidden:
        raise ValueError(f"items[{index}] exposes private payload fields: {', '.join(forbidden)}")

    for field in (
        "review_item_id",
        "source_finding_or_intake_id",
        "publisher",
        "title",
        "exact_supporting_passage",
        "proposed_public_headline",
        "proposed_public_summary",
        "location_name",
        "location_scope",
        "pressure_type",
        "why_it_matters",
        "evidence_level",
        "confidence",
        "proposed_section",
    ):
        if not str(item.get(field) or "").strip():
            raise ValueError(f"items[{index}].{field} must not be empty")

    _validate_source_artifact_path(item["source_artifact_path"])
    source_url = _require_https(item["source_url"], f"items[{index}].source_url")
    canonical_url = _require_https(item["canonical_source_url"], f"items[{index}].canonical_source_url")
    if _article_url_identity(source_url) != _article_url_identity(canonical_url):
        raise ValueError(f"items[{index}] source and canonical URLs must identify the same article")
    source_published_at = _require_date(item["source_published_at"], f"items[{index}].source_published_at")
    source_date = date.fromisoformat(source_published_at[:10])

    affected_groups = item["affected_groups"]
    if not isinstance(affected_groups, list) or any(not isinstance(value, str) for value in affected_groups):
        raise ValueError(f"items[{index}].affected_groups must be a string list")
    duplicate_check = item["duplicate_check"]
    if not isinstance(duplicate_check, dict) or not str(duplicate_check.get("status") or "").strip():
        raise ValueError(f"items[{index}].duplicate_check must include status")
    if duplicate_check["status"] != "not_published":
        raise ValueError(f"items[{index}] is not eligible for the current queue because duplicate status is not 'not_published'")
    freshness_check = item["freshness_check"]
    if not isinstance(freshness_check, dict) or freshness_check.get("status") != "current":
        raise ValueError(f"items[{index}] is not current enough for the proposed daily edition")
    age_days = (edition_date - source_date).days
    if age_days < 0 or age_days > CURRENT_FRESHNESS_WINDOW_DAYS:
        raise ValueError(f"items[{index}] is outside the {CURRENT_FRESHNESS_WINDOW_DAYS}-day current review window")
    try:
        reported_age_days = int(freshness_check.get("age_days"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"items[{index}].freshness_check.age_days must be an integer") from exc
    if reported_age_days != age_days or freshness_check.get("edition_date") != edition_date.isoformat():
        raise ValueError(f"items[{index}].freshness_check does not match the source and edition dates")

    status = str(item["editorial_status"] or "").strip()
    if status not in ALLOWED_EDITORIAL_STATUSES:
        raise ValueError(f"items[{index}].editorial_status is invalid")
    if item["publication_eligible"] is not False:
        raise ValueError(f"items[{index}].publication_eligible must remain false in private editorial review")
    try:
        rank = int(item["proposed_rank"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"items[{index}].proposed_rank must be a positive integer") from exc
    if rank < 1:
        raise ValueError(f"items[{index}].proposed_rank must be a positive integer")
    return item


def validate_queue(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("current review queue must be a JSON object")
    if payload.get("schema_version") != QUEUE_SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {QUEUE_SCHEMA_VERSION}")
    edition_date_text = _require_date(payload.get("edition_date"), "edition_date")
    edition_date = date.fromisoformat(edition_date_text[:10])
    if payload.get("production_scope") != CURRENT_PRODUCTION_SCOPE:
        raise ValueError(f"production_scope must be {CURRENT_PRODUCTION_SCOPE}")
    excluded_roots = tuple(payload.get("historical_roots_excluded") or ())
    if excluded_roots != HISTORICAL_ROOTS:
        raise ValueError("historical_roots_excluded must name both protected historical roots")
    if tuple(payload.get("allowed_decisions") or ()) != ALLOWED_DECISIONS:
        raise ValueError("allowed_decisions does not match the editorial decision contract")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    validated = [_validate_item(item, index, edition_date=edition_date) for index, item in enumerate(items)]
    review_ids = [str(item["review_item_id"]) for item in validated]
    if len(review_ids) != len(set(review_ids)):
        raise ValueError("review_item_id values must be unique")
    ranks = [int(item["proposed_rank"]) for item in validated]
    if len(ranks) != len(set(ranks)):
        raise ValueError("proposed_rank values must be unique")
    return payload


def load_queue(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"current review queue not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"current review queue is invalid JSON: {path}") from exc
    return validate_queue(payload)


def queue_summary(payload: dict[str, Any]) -> dict[str, Any]:
    queue = validate_queue(payload)
    statuses = Counter(str(item["editorial_status"]) for item in queue["items"])
    return {
        "ok": True,
        "queue_id": queue.get("queue_id"),
        "edition_date": queue["edition_date"],
        "item_count": len(queue["items"]),
        "editorial_status_counts": dict(sorted(statuses.items())),
        "publication_eligible_count": 0,
        "historical_roots_excluded": list(HISTORICAL_ROOTS),
        "queue_sha256": payload_sha256(queue),
    }


def apply_editorial_decision(
    queue: dict[str, Any],
    *,
    review_item_id: str,
    decision: str,
    decided_by: str,
    decided_at: str,
    editorial_note: str,
    proposed_public_headline: str | None = None,
    proposed_public_summary: str | None = None,
) -> dict[str, Any]:
    validate_queue(queue)
    if decision not in ALLOWED_DECISIONS:
        raise ValueError(f"decision must be one of: {', '.join(ALLOWED_DECISIONS)}")
    if not str(decided_by or "").strip():
        raise ValueError("decided_by is required")
    _require_date(decided_at, "decided_at")
    matches = [item for item in queue["items"] if item["review_item_id"] == review_item_id]
    if len(matches) != 1:
        raise ValueError(f"review_item_id must match exactly one queue item: {review_item_id}")
    item = matches[0]
    if decision == "approve_with_edit" and not (proposed_public_headline or proposed_public_summary):
        raise ValueError("approve_with_edit requires an edited headline or summary")
    if proposed_public_headline is not None:
        if not proposed_public_headline.strip():
            raise ValueError("proposed_public_headline must not be empty")
        item["proposed_public_headline"] = proposed_public_headline.strip()
    if proposed_public_summary is not None:
        if not proposed_public_summary.strip():
            raise ValueError("proposed_public_summary must not be empty")
        item["proposed_public_summary"] = proposed_public_summary.strip()
    item["editorial_status"] = decision
    item["editorial_note"] = str(editorial_note or "").strip()
    item["decision_audit"] = {
        "decided_at": decided_at,
        "decided_by": decided_by.strip(),
        "decision": decision,
    }
    item["publication_eligible"] = False
    validate_queue(queue)
    return queue


def build_proposed_edition(queue: dict[str, Any]) -> dict[str, Any]:
    validated = validate_queue(queue)
    proposed_items = sorted(
        (
            item
            for item in validated["items"]
            if item["editorial_status"] in {"pending_editorial_review", "approve", "approve_with_edit"}
        ),
        key=lambda item: (int(item["proposed_rank"]), str(item["review_item_id"])),
    )[:6]
    blocked = not proposed_items
    public_items = [
        {
            "rank": int(item["proposed_rank"]),
            "headline": item["proposed_public_headline"],
            "summary": item["proposed_public_summary"],
            "location_name": item["location_name"],
            "state": item["state"],
            "source": item["publisher"],
            "source_url": item["canonical_source_url"],
            "source_published_at": item["source_published_at"],
            "why_it_matters": item["why_it_matters"],
            "uncertainty_note": item["uncertainty_note"],
            "section": item["proposed_section"],
        }
        for item in proposed_items
    ]
    publisher_counts = Counter(item["source"] for item in public_items)
    state_counts = Counter(item["state"] or "unspecified" for item in public_items)
    status = "blocked_no_reviewable_current_signals" if blocked else "draft_pending_editorial_review"
    generation_note = (
        "No reader-facing edition was assembled because the private current queue contains no reviewable current signals."
        if blocked
        else "Private editorial preview assembled from current-review items that are pending or approved; no public output was generated."
    )
    return {
        "schema_version": PROPOSED_EDITION_SCHEMA_VERSION,
        "edition_date": validated["edition_date"],
        "draft_status": status,
        "draft": True,
        "published": False,
        "publication_eligible": False,
        "publication_approval": False,
        "source_queue_path": PRIVATE_QUEUE_PATH.as_posix(),
        "source_queue_sha256": payload_sha256(validated),
        "selected_item_count": len(public_items),
        "items": public_items,
        "layout": {
            "eyebrow_status": "FOOD LINE — PRIVATE DRAFT / UNPUBLISHED",
            "generation_note": generation_note,
            "h1": f"Food Line Dispatch — {validated['edition_date']}",
            "edition_summary": (
                "No proposed current edition is available for editorial decision."
                if blocked
                else f"{len(public_items)} current, source-traceable food-pressure signals are proposed for editorial review."
            ),
            "todays_read": public_items[:1],
            "at_a_glance": public_items,
            "core_food_pressure_signals": [item for item in public_items if item["section"] == "Core Food Pressure Signals"],
            "other_food_line_signals": [item for item in public_items if item["section"] == "Other Food Line Signals"],
            "source_mix": {
                "publishers": dict(sorted(publisher_counts.items())),
                "states": dict(sorted(state_counts.items())),
            },
            "source_note": "Every proposed item retains its canonical source URL and source publication date. Inclusion in this preview and editorial approval do not grant publication authority.",
        },
    }


def render_operator_markdown(queue: dict[str, Any], proposed: dict[str, Any]) -> str:
    lines = [
        f"# Food Line proposed edition — {proposed['edition_date']}",
        "",
        "**PRIVATE DRAFT — UNPUBLISHED — NOT PUBLICATION-ELIGIBLE**",
        "",
        str(proposed["layout"]["generation_note"]),
        "",
        "## Proposed reader-facing preview",
        "",
    ]
    if not proposed["items"]:
        lines.extend(
            [
                "No edition items were proposed. No current nonhistorical repository signal is available for editorial review.",
                "",
            ]
        )
    else:
        for item in proposed["items"]:
            lines.extend(
                [
                    f"### {item['rank']}. {item['headline']}",
                    "",
                    f"{item['summary']}",
                    "",
                    f"Why it matters: {item['why_it_matters']}",
                    "",
                    f"Source: [{item['source']}]({item['source_url']}) — {item['source_published_at']}",
                    "",
                    f"Uncertainty: {item['uncertainty_note'] or 'None recorded.'}",
                    "",
                ]
            )
    lines.extend(["## Operator decision summary", ""])
    if not queue["items"]:
        lines.extend(
            [
                "The queue is empty. Supply or import a current nonhistorical Food Line finding before an editorial decision can be recorded.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "| Rank | Headline | Location | Source | Documented pressure | Strongest evidence | Primary uncertainty | Duplicate | Recommendation | Decisions |",
                "|---:|---|---|---|---|---|---|---|---|---|",
            ]
        )
        for item in sorted(queue["items"], key=lambda row: int(row["proposed_rank"])):
            values = (
                item["proposed_rank"],
                item["proposed_public_headline"],
                f"{item['location_name']}, {item['state']}".strip(", "),
                item["publisher"],
                item["why_it_matters"],
                item["exact_supporting_passage"],
                item["uncertainty_note"] or "None recorded",
                item["duplicate_check"].get("status"),
                item["editorial_note"] or "pending editorial review",
                "approve / approve_with_edit / hold / reject",
            )
            escaped = [str(value).replace("|", "\\|").replace("\n", " ") for value in values]
            lines.append("| " + " | ".join(escaped) + " |")
        lines.append("")
    lines.extend(
        [
            "## Safety boundary",
            "",
            "This preview does not render public HTML, update a publication ledger, approve publication, generate audio or social artifacts, or touch Pages.",
            "",
        ]
    )
    return "\n".join(lines)


def write_proposed_edition(root: Path, queue: dict[str, Any]) -> tuple[Path, Path, dict[str, Any]]:
    proposed = build_proposed_edition(queue)
    edition_date = proposed["edition_date"]
    json_path = root / PRIVATE_PROPOSED_EDITION_ROOT / f"{edition_date}.json"
    markdown_path = root / PRIVATE_PROPOSED_EDITION_ROOT / f"{edition_date}.md"
    write_json_atomic(json_path, proposed)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_operator_markdown(queue, proposed), encoding="utf-8", newline="\n")
    return json_path, markdown_path, proposed
