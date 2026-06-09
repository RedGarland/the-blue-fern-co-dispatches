from __future__ import annotations

__test__ = False

import argparse
import csv
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.food_line_sources import (  # noqa: E402
    DEFAULT_AFFECTED_GROUP_KEYWORDS,
    DEFAULT_NEGATIVE_KEYWORDS,
    _extract_page_evidence,
    _extract_page_metadata_date,
    _date_provenance_warning,
    _fetch,
    _parse_rss_items,
    _pressure_match_terms,
    _url_path_date,
    _ensure_candidate_lifecycle_fields,
    classify_food_line_source_purpose,
    evaluate_food_line_pressure,
    load_food_line_candidate_registry,
    load_food_line_source_performance_history,
    refresh_food_line_pressure_registry_source_purpose,
    save_food_line_source_performance_history,
    upsert_food_line_source_performance_history,
    food_line_test_mode_enabled,
    resolve_food_line_fetcher,
)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _nonempty(value: Any) -> str:
    return str(value or "").strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _candidate_review_paths(root: Path, date: str) -> tuple[Path, Path]:
    review_path = root / "output" / "review" / "food-line" / date / "candidate_source_review.csv"
    audit_path = root / "data" / "dispatches" / "food-line" / "sources" / date / "candidate_source_audit.json"
    return review_path, audit_path


def _candidate_promotion_path(root: Path, date: str) -> Path:
    return root / "output" / "review" / "food-line" / date / "candidate_promotion_report.csv"


def _candidate_registry_path(root: Path) -> Path:
    return root / "data" / "dispatches" / "food-line" / "candidate_source_registry.json"


def _pressure_registry_path(root: Path) -> Path:
    return root / "data" / "dispatches" / "food-line" / "pressure_source_registry.json"


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must be a list")
    return [row for row in payload if isinstance(row, dict)]


def _write_json(path: Path, payload: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _candidate_source_type(candidate: dict[str, Any]) -> str:
    basis = str(candidate.get("expected_text_basis") or "manual").strip().lower()
    url = str(candidate.get("candidate_url") or "").strip().lower()
    if basis in {"rss_summary", "rss_title"} or url.endswith((".rss", ".xml")):
        return "rss"
    if basis == "api_json" or url.endswith(".json"):
        return "api"
    return "page"


def _candidate_useful_text_available(items: list[dict[str, Any]]) -> bool:
    for item in items:
        text = " ".join(str(item.get(field) or "").strip() for field in ("title", "summary_or_snippet", "evidence_text")).strip()
        if text:
            return True
    return False


def _candidate_negative_hit_count(items: list[dict[str, Any]]) -> int:
    hits = 0
    negative_terms = [str(term).strip().lower() for term in DEFAULT_NEGATIVE_KEYWORDS]
    for item in items:
        text = " ".join(str(item.get(field) or "") for field in ("title", "summary_or_snippet", "evidence_text")).lower()
        if any(term and term in text for term in negative_terms):
            hits += 1
    return hits


def _candidate_noise_score(item_count: int, negative_hit_count: int, rejected_item_count: int) -> int:
    if item_count <= 0:
        return 100
    noisy_items = max(negative_hit_count, rejected_item_count)
    return min(100, int(round((noisy_items / item_count) * 100)))


def _candidate_pressure_hit_rate(item_count: int, candidate_pressure_item_count: int) -> float:
    if item_count <= 0:
        return 0.0
    return round(candidate_pressure_item_count / item_count, 3)


def _candidate_quality_score(
    *,
    recommendation: str,
    useful_text_available: bool,
    pressure_hit_rate: float,
    noise_score: int,
    fetch_error: str,
    candidate_pressure_item_count: int,
    source_purpose: str,
) -> int:
    score = 40
    if useful_text_available:
        score += 15
    score += min(20, int(round(pressure_hit_rate * 20)))
    score -= min(25, max(0, noise_score - 30) // 2)
    if fetch_error:
        score -= 20
    if candidate_pressure_item_count:
        score += 10
    if source_purpose in {"donation_page", "evergreen_context", "resource_page", "program_description"}:
        score -= 25
    if recommendation == "enable":
        score += 15
    elif recommendation == "keep_candidate":
        score += 5
    elif recommendation == "reject":
        score -= 10
    return max(0, min(100, score))


def _candidate_quality_tier(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 45:
        return "medium"
    if score >= 15:
        return "low"
    return "quarantine"


def _should_skip_quarantined(status: str, include_quarantined: bool) -> bool:
    return status == "quarantined" and not include_quarantined


def _candidate_registry_entry(candidate: dict[str, Any]) -> dict[str, Any]:
    source_type = _candidate_source_type(candidate)
    pressure_required = True
    purpose = classify_food_line_source_purpose(
        {
            "source_id": candidate.get("source_id"),
            "source_name": candidate.get("source_name"),
            "publisher": candidate.get("publisher"),
            "candidate_url": candidate.get("candidate_url"),
            "source_family": candidate.get("source_family"),
            "source_type": source_type,
            "state": candidate.get("state"),
            "location_name": candidate.get("location_name"),
            "location_scope": candidate.get("location_scope"),
            "candidate_reason": candidate.get("candidate_reason"),
            "expected_text_basis": candidate.get("expected_text_basis"),
            "extraction_quality_guess": candidate.get("extraction_quality_guess"),
            "notes": candidate.get("notes"),
        }
    )
    return {
        "source_id": str(candidate.get("source_id") or "").strip(),
        "source_name": str(candidate.get("source_name") or "").strip(),
        "publisher": str(candidate.get("publisher") or "").strip(),
        "source_family": str(candidate.get("source_family") or "").strip(),
        "source_type": source_type,
        "url": str(candidate.get("candidate_url") or "").strip(),
        "state": str(candidate.get("state") or "").strip().upper(),
        "location_name": str(candidate.get("location_name") or "").strip(),
        "location_scope": str(candidate.get("location_scope") or "").strip(),
        "extraction_quality": str(candidate.get("extraction_quality_guess") or "unknown").strip().lower(),
        "expected_text_basis": str(candidate.get("expected_text_basis") or "manual").strip().lower(),
        "pressure_verification_required": pressure_required,
        "pressure_required": pressure_required,
        "positive_keywords": list(candidate.get("pressure_topics_expected") or []),
        "negative_keywords": list(DEFAULT_NEGATIVE_KEYWORDS),
        "affected_group_keywords": list(DEFAULT_AFFECTED_GROUP_KEYWORDS.keys()),
        "enabled": True,
        "notes": str(candidate.get("notes") or candidate.get("candidate_reason") or "").strip(),
        "freshness_mode": "pressure",
        "max_age_days": 7 if source_type in {"rss", "feed"} else 14,
        "source_role_allowed": "pressure_evidence",
        "source_purpose": purpose["source_purpose"],
        "current_or_evergreen": purpose["current_or_evergreen"],
        "promotable": purpose["promotable"] == "true",
        "non_promotable_reason": purpose["non_promotable_reason"],
    }


def _merge_candidate_row(existing: dict[str, Any], incoming: dict[str, str]) -> dict[str, Any]:
    merged = dict(existing)

    def set_if_present(key: str, incoming_key: str | None = None) -> None:
        source_key = incoming_key or key
        value = _nonempty(incoming.get(source_key))
        if value:
            merged[key] = value

    set_if_present("source_name")
    set_if_present("publisher")
    set_if_present("candidate_url")
    set_if_present("source_family")
    set_if_present("source_type")
    set_if_present("state")
    set_if_present("location_name")
    set_if_present("location_scope")
    set_if_present("candidate_reason")
    set_if_present("expected_text_basis")
    set_if_present("extraction_quality_guess")
    set_if_present("source_type")
    set_if_present("source_purpose")
    set_if_present("current_or_evergreen")
    set_if_present("non_promotable_reason")
    set_if_present("status")
    set_if_present("notes")

    pressure_topics = _nonempty(incoming.get("pressure_topics_expected"))
    if pressure_topics:
        merged["pressure_topics_expected"] = [part.strip() for part in pressure_topics.split("|") if part.strip()]

    return merged


def import_food_line_candidate_intake(root: Path, csv_path: Path) -> dict[str, Any]:
    registry_path = _candidate_registry_path(root)
    existing_rows = _read_json_list(registry_path)
    existing_by_source_id = {str(row.get("source_id") or "").strip(): row for row in existing_rows if str(row.get("source_id") or "").strip()}
    imported_rows: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []
    seen_source_ids: set[str] = set()
    imported_count = 0
    updated_count = 0
    skipped_count = 0
    rejected_count = 0
    valid_source_types = {"rss", "page", "api"}
    valid_statuses = {"candidate", "tested_good", "tested_weak", "tested_failed", "enabled", "rejected", "promoted"}

    for row in _read_csv_rows(csv_path):
        source_id = _nonempty(row.get("source_id"))
        source_name = _nonempty(row.get("source_name"))
        candidate_url = _nonempty(row.get("candidate_url"))
        source_family = _nonempty(row.get("source_family"))
        source_type = _nonempty(row.get("source_type")).lower()
        status = _nonempty(row.get("status")).lower()

        if not any(_nonempty(value) for value in row.values()):
            skipped_count += 1
            report_rows.append({"source_id": "", "source_name": "", "candidate_url": "", "action": "skipped", "reason": "blank template row"})
            continue
        if not source_id and not source_name and not candidate_url and not source_family:
            skipped_count += 1
            report_rows.append({"source_id": "", "source_name": "", "candidate_url": "", "action": "skipped", "reason": "blank template row"})
            continue
        if not source_id:
            rejected_count += 1
            report_rows.append({"source_id": "", "source_name": source_name, "candidate_url": candidate_url, "action": "rejected", "reason": "missing source_id"})
            continue
        if source_id in seen_source_ids:
            rejected_count += 1
            report_rows.append({"source_id": source_id, "source_name": source_name, "candidate_url": candidate_url, "action": "rejected", "reason": "duplicate source_id in CSV"})
            continue
        seen_source_ids.add(source_id)
        if not source_name:
            rejected_count += 1
            report_rows.append({"source_id": source_id, "source_name": source_name, "candidate_url": candidate_url, "action": "rejected", "reason": "missing source_name"})
            continue
        if not candidate_url:
            rejected_count += 1
            report_rows.append({"source_id": source_id, "source_name": source_name, "candidate_url": candidate_url, "action": "rejected", "reason": "missing candidate_url"})
            continue
        if not source_family:
            rejected_count += 1
            report_rows.append({"source_id": source_id, "source_name": source_name, "candidate_url": candidate_url, "action": "rejected", "reason": "missing source_family"})
            continue
        if not candidate_url.startswith(("http://", "https://")):
            rejected_count += 1
            report_rows.append({"source_id": source_id, "source_name": source_name, "candidate_url": candidate_url, "action": "rejected", "reason": "candidate_url must use http or https"})
            continue
        if source_type and source_type not in valid_source_types:
            rejected_count += 1
            report_rows.append({"source_id": source_id, "source_name": source_name, "candidate_url": candidate_url, "action": "rejected", "reason": f"invalid source_type: {source_type}"})
            continue
        if status and status not in valid_statuses:
            rejected_count += 1
            report_rows.append({"source_id": source_id, "source_name": source_name, "candidate_url": candidate_url, "action": "rejected", "reason": f"invalid status: {status}"})
            continue

        incoming = {
            "source_id": source_id,
            "source_name": source_name,
            "publisher": _nonempty(row.get("publisher")),
            "candidate_url": candidate_url,
            "source_family": source_family,
            "source_type": source_type,
            "state": _nonempty(row.get("state")).upper(),
            "location_name": _nonempty(row.get("location_name")),
            "location_scope": _nonempty(row.get("location_scope")),
            "candidate_reason": _nonempty(row.get("candidate_reason")),
            "expected_text_basis": _nonempty(row.get("expected_text_basis")),
            "extraction_quality_guess": _nonempty(row.get("extraction_quality_guess")),
            "pressure_topics_expected": [part.strip() for part in _nonempty(row.get("pressure_topics_expected")).split("|") if part.strip()],
            "status": status or "candidate",
            "notes": _nonempty(row.get("notes")),
        }
        if source_id in existing_by_source_id:
            existing_by_source_id[source_id] = _merge_candidate_row(existing_by_source_id[source_id], row)
            updated_count += 1
            report_rows.append({"source_id": source_id, "source_name": source_name, "candidate_url": candidate_url, "action": "updated", "reason": "existing candidate updated"})
        else:
            new_row = {
                "source_id": source_id,
                "source_name": source_name,
                "publisher": incoming["publisher"],
                "candidate_url": candidate_url,
                "source_family": source_family,
                "source_type": incoming["source_type"] or "rss",
                "state": incoming["state"] or "US",
                "location_name": incoming["location_name"] or "",
                "location_scope": incoming["location_scope"] or ("national" if incoming["state"] in {"", "US"} else "state_local"),
                "candidate_reason": incoming["candidate_reason"],
                "expected_text_basis": incoming["expected_text_basis"] or "manual",
                "extraction_quality_guess": incoming["extraction_quality_guess"] or "unknown",
                "pressure_topics_expected": incoming["pressure_topics_expected"],
                "status": incoming["status"] or "candidate",
                "notes": incoming["notes"],
            }
            existing_by_source_id[source_id] = new_row
            imported_count += 1
            report_rows.append({"source_id": source_id, "source_name": source_name, "candidate_url": candidate_url, "action": "inserted", "reason": "new candidate imported"})

    imported_rows = sorted(existing_by_source_id.values(), key=lambda row: str(row.get("source_id") or ""))
    _write_json(registry_path, imported_rows)
    report_path = root / "output" / "review" / "food-line" / "candidate_intake_import_report.csv"
    _write_csv(report_path, ["source_id", "source_name", "candidate_url", "action", "reason"], report_rows)
    summary = {
        "imported_count": imported_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "rejected_count": rejected_count,
        "registry_path": str(registry_path),
        "report_path": str(report_path),
    }
    print(json.dumps(summary, indent=2))
    return summary


def cleanup_food_line_candidates(root: Path, *, mode: str = "conservative", dry_run: bool = False) -> dict[str, Any]:
    path = _candidate_registry_path(root)
    rows = [_ensure_candidate_lifecycle_fields(row) for row in _read_json_list(path)]
    report_rows: list[dict[str, Any]] = []
    latest_review_by_source_id = _latest_candidate_review_rows(root)
    quarantined_count = 0
    archived_count = 0
    preserved_enabled_count = 0
    mode = str(mode or "conservative").strip().lower()
    if mode not in {"conservative", "normal", "aggressive"}:
        mode = "conservative"

    for row in rows:
        source_id = str(row.get("source_id") or "").strip()
        previous_status = str(row.get("status") or "candidate").lower()
        reject_count = int(row.get("reject_count") or 0)
        history = load_food_line_source_performance_history(root).get(source_id, {})
        latest_review = latest_review_by_source_id.get(source_id, {})
        recommended_action, reason = _source_registry_health_action(row=row, history=history, latest_review=latest_review, mode=mode)
        new_status = previous_status
        fetch_failures = int(history.get("fetch_failures") or 0)
        rolling_quality_score = int(history.get("rolling_quality_score") or row.get("source_quality_score") or 0)
        useful_text_available = str(latest_review.get("useful_text_available") or "").lower() == "true" or rolling_quality_score >= 20
        if recommended_action == "preserve_enabled":
            new_status = "enabled"
            preserved_enabled_count += 1
        elif recommended_action == "quarantine":
            new_status = "quarantined"
            quarantined_count += 1
        elif recommended_action == "archive":
            new_status = "archived"
            archived_count += 1
        if not dry_run:
            row["status"] = new_status
            row["last_recommendation"] = "cleanup"
            row["last_recommendation_reason"] = reason
            row["source_quality_score"] = _candidate_quality_score(
                recommendation="reject" if new_status in {"quarantined", "archived"} else "keep_candidate",
                useful_text_available=useful_text_available,
                pressure_hit_rate=0.0,
                noise_score=100 if new_status in {"quarantined", "archived"} else 0,
                fetch_error=str(history.get("last_fetch_error") or ""),
                candidate_pressure_item_count=int(history.get("verified_pressure_records") or 0),
                source_purpose=str(row.get("source_purpose") or "unknown"),
            )
            row["source_quality_tier"] = _candidate_quality_tier(int(row.get("source_quality_score") or 0))
        report_rows.append(
            {
                "source_id": source_id,
                "source_name": row.get("source_name") or "",
                "previous_status": previous_status,
                "new_status": new_status,
                "reason": reason,
                "source_quality_score": row.get("source_quality_score") or 0,
                "reject_count": reject_count,
                "fetch_failures": fetch_failures,
                "useful_text_available": str(useful_text_available).lower(),
            }
        )
    if not dry_run:
        _write_candidate_registry(root, rows)
    report_path = root / "output" / "review" / "food-line" / "candidate_cleanup_report.csv"
    _write_csv(
        report_path,
        ["source_id", "source_name", "previous_status", "new_status", "reason", "source_quality_score", "reject_count", "fetch_failures", "useful_text_available"],
        report_rows,
    )
    health_report_path = root / "output" / "review" / "food-line" / "source_registry_health_report.csv"
    current_history = load_food_line_source_performance_history(root)
    _write_csv(
        health_report_path,
        [
            "source_id",
            "source_name",
            "status",
            "source_family",
            "state",
            "source_quality_score",
            "source_quality_tier",
            "test_count",
            "reject_count",
            "keep_candidate_count",
            "enable_count",
            "fetch_failures",
            "useful_text_available",
            "last_recommendation",
            "recommended_action",
        ],
        [
            {
                "source_id": row.get("source_id") or "",
                "source_name": row.get("source_name") or "",
                "status": row.get("status") or "",
                "source_family": row.get("source_family") or "",
                "state": row.get("state") or "",
                "source_quality_score": row.get("source_quality_score") or 0,
                "source_quality_tier": row.get("source_quality_tier") or "",
                "test_count": row.get("test_count") or 0,
                "reject_count": row.get("reject_count") or 0,
                "keep_candidate_count": row.get("keep_candidate_count") or 0,
                "enable_count": row.get("enable_count") or 0,
                "fetch_failures": int(current_history.get(str(row.get("source_id") or ""), {}).get("fetch_failures") or 0),
                "useful_text_available": str(latest_review_by_source_id.get(str(row.get("source_id") or ""), {}).get("useful_text_available") or (int(row.get("source_quality_score") or 0) >= 20)).lower(),
                "last_recommendation": row.get("last_recommendation") or "",
                "recommended_action": _source_registry_health_action(
                    row=row,
                    history=current_history.get(str(row.get("source_id") or ""), {}),
                    latest_review=latest_review_by_source_id.get(str(row.get("source_id") or ""), {}),
                    mode=mode,
                )[0],
            }
            for row in rows
        ],
    )
    summary = {
        "ok": True,
        "candidate_count": len(rows),
        "candidate_count_before": len(rows),
        "candidate_count_after": len(rows),
        "quarantined_count": quarantined_count,
        "archived_count": archived_count,
        "preserved_enabled_count": preserved_enabled_count,
        "dry_run": dry_run,
        "mode": mode,
        "cleanup_report_path": str(report_path),
        "source_registry_health_report_path": str(health_report_path),
        "registry_path": str(path),
    }
    print(json.dumps(summary, indent=2))
    return summary


def _upsert_pressure_registry(root: Path, promoted_candidates: list[dict[str, Any]]) -> tuple[Path, list[dict[str, str]]]:
    registry_path = _pressure_registry_path(root)
    existing = _read_json_list(registry_path)
    updated = list(existing)
    changes: list[dict[str, str]] = []
    index_by_source_id = {str(row.get("source_id") or ""): idx for idx, row in enumerate(updated) if str(row.get("source_id") or "")}
    for candidate in promoted_candidates:
        entry = _candidate_registry_entry(candidate)
        source_id = entry["source_id"]
        if not source_id:
            continue
        if source_id in index_by_source_id:
            idx = index_by_source_id[source_id]
            merged = dict(updated[idx])
            merged.update(entry)
            updated[idx] = merged
            changes.append({"source_id": source_id, "action": "updated"})
        else:
            updated.append(entry)
            index_by_source_id[source_id] = len(updated) - 1
            changes.append({"source_id": source_id, "action": "added"})
    _write_json(registry_path, updated)
    return registry_path, changes


def _update_candidate_registry_statuses(root: Path, promoted_ids: set[str]) -> Path:
    path = _candidate_registry_path(root)
    payload = _read_json_list(path)
    for row in payload:
        source_id = str(row.get("source_id") or "").strip()
        if source_id in promoted_ids:
            row["status"] = "enabled"
    _write_json(path, payload)
    return path


def _write_candidate_registry(root: Path, rows: list[dict[str, Any]]) -> Path:
    path = _candidate_registry_path(root)
    normalized = [_ensure_candidate_lifecycle_fields(row) for row in rows]
    normalized.sort(key=lambda row: str(row.get("source_id") or ""))
    _write_json(path, normalized)
    return path


def _latest_candidate_review_rows(root: Path) -> dict[str, dict[str, Any]]:
    review_root = root / "output" / "review" / "food-line"
    if not review_root.exists():
        return {}
    review_paths = sorted(review_root.glob("*/candidate_source_review.csv"))
    if not review_paths:
        return {}
    latest_path = review_paths[-1]
    rows = _read_csv_rows(latest_path)
    return {str(row.get("source_id") or "").strip(): row for row in rows if str(row.get("source_id") or "").strip()}


def _source_registry_health_action(
    *,
    row: dict[str, Any],
    history: dict[str, Any],
    latest_review: dict[str, Any],
    mode: str,
) -> tuple[str, str]:
    status = str(row.get("status") or "candidate").lower()
    if status == "enabled":
        return "preserve_enabled", "enabled source preserved"

    reject_count = int(row.get("reject_count") or 0)
    test_count = int(row.get("test_count") or 0)
    fetch_failures = int(history.get("fetch_failures") or 0)
    useful_text_available = str(latest_review.get("useful_text_available") or "").lower() == "true"
    latest_recommendation = str(latest_review.get("recommendation") or row.get("last_recommendation") or "").lower()
    latest_noise_score = int(latest_review.get("noise_score") or row.get("source_quality_score") or 0)
    verified_pressure = int(history.get("verified_pressure_records") or 0)
    source_quality_score = int(row.get("source_quality_score") or history.get("rolling_quality_score") or 0)
    source_family = str(row.get("source_family") or "").lower()
    high_value_family = source_family in {"local_news", "public_radio", "nonprofit_news", "food_bank_provider", "state_official", "federal_official", "disaster_emergency", "school_meals_child_nutrition", "senior_meals"}

    if mode in {"normal", "aggressive"}:
        if fetch_failures >= 3:
            return "archive", "inaccessible or broken after repeated failures"
        if reject_count >= 3 and not useful_text_available:
            return "archive", "three or more rejects with no useful text"
    if mode in {"conservative", "normal", "aggressive"}:
        if reject_count >= 2 and (test_count >= 2 or latest_recommendation in {"reject", "skip_quarantined"}):
            return "quarantine", "repeated rejects across test runs"
        if fetch_failures >= 2 and (test_count >= 2 or latest_review):
            return "quarantine", "repeated fetch failures"
        if not useful_text_available and test_count >= 2:
            return "quarantine", "no useful text across test runs"
    if mode in {"normal", "aggressive"}:
        if latest_noise_score >= 75 and verified_pressure == 0:
            return "quarantine", "high noise and no verified pressure"
    if mode == "aggressive":
        if verified_pressure == 0 and not high_value_family and source_quality_score < 25:
            return "archive", "low-quality source without verified pressure"
    return "keep_candidate", "preserved"


def _candidate_recommendation(
    *,
    fetched: bool,
    item_count: int,
    accepted_pressure_item_count: int,
    rejected_item_count: int,
    useful_text_available: bool,
    noise_score: int,
    pressure_hit_rate: float,
    fetch_error: str,
    top_rejection_reasons: list[str],
    source_status: str,
    candidate_pressure_item_count: int,
    source_purpose: str,
    promotable: bool,
    non_promotable_reason: str,
) -> tuple[str, str]:
    if source_purpose == "donation_page":
        return "reject", non_promotable_reason or "donation page is not current pressure evidence"
    if source_purpose in {"evergreen_context", "resource_page", "program_description"}:
        if fetch_error:
            return "reject", "broken feed or inaccessible source"
        if not fetched or item_count == 0 or not useful_text_available:
            return "reject", "no usable text"
        return "keep_candidate", non_promotable_reason or "source is not current pressure evidence"
    if fetch_error:
        return "reject", "broken feed or inaccessible source"
    if not fetched or item_count == 0 or not useful_text_available:
        return "reject", "no usable text"
    if noise_score >= 75 and accepted_pressure_item_count == 0:
        return "reject", "mostly recipes/restaurants/lifestyle noise"
    if accepted_pressure_item_count > 0 and promotable:
        return "enable", "source exposes verified pressure evidence"
    if source_status == "tested_good" and pressure_hit_rate >= 0.5 and candidate_pressure_item_count > 0 and promotable:
        return "enable", "source is high-value and exposes recurring pressure evidence"
    return "keep_candidate", "feed works but no current pressure item is found"


def _evaluate_candidate_source(root: Path, candidate: dict[str, Any], date: str, fetch: Any, *, include_quarantined: bool = False) -> dict[str, Any]:
    performance = load_food_line_source_performance_history(root).get(str(candidate.get("source_id") or ""), {})
    if _should_skip_quarantined(str(candidate.get("status") or "").strip().lower(), include_quarantined):
        source_id = str(candidate.get("source_id") or "")
        source_name = str(candidate.get("source_name") or "")
        candidate_url = str(candidate.get("candidate_url") or "")
        review_row = {
            "source_id": source_id,
            "source_name": source_name,
            "publisher": str(candidate.get("publisher") or ""),
            "candidate_url": candidate_url,
            "state": str(candidate.get("state") or ""),
            "source_family": str(candidate.get("source_family") or ""),
            "fetched": "false",
            "fetch_error": "",
            "item_count": 0,
            "candidate_pressure_item_count": 0,
            "accepted_pressure_item_count": 0,
            "rejected_item_count": 0,
            "noise_score": 100,
            "pressure_hit_rate": 0.0,
            "negative_hit_count": 0,
            "useful_text_available": "false",
            "source_purpose": str(candidate.get("source_purpose") or "unknown"),
            "current_or_evergreen": str(candidate.get("current_or_evergreen") or "unknown"),
            "promotable": str(candidate.get("promotable") or False).lower(),
            "non_promotable_reason": str(candidate.get("non_promotable_reason") or ""),
            "top_pressure_terms": "",
            "sample_accepted_titles": "",
            "sample_rejected_titles": "",
            "recommendation": "skip_quarantined",
            "reason": "quarantined candidate skipped",
            "source_quality_score": int(candidate.get("source_quality_score") or 0),
            "source_quality_tier": str(candidate.get("source_quality_tier") or "quarantine"),
            "first_discovered_at": str(candidate.get("first_discovered_at") or ""),
            "last_discovered_at": str(candidate.get("last_discovered_at") or ""),
            "last_tested_at": "",
            "discovery_count": int(candidate.get("discovery_count") or 0),
            "test_count": int(candidate.get("test_count") or 0),
            "enable_count": int(candidate.get("enable_count") or 0),
            "reject_count": int(candidate.get("reject_count") or 0),
            "keep_candidate_count": int(candidate.get("keep_candidate_count") or 0),
            "last_recommendation": "skip_quarantined",
            "last_recommendation_reason": "quarantined candidate skipped",
            "auto_discovered": bool(candidate.get("auto_discovered", False)),
        }
        audit_row = {
            "source_id": source_id,
            "source_name": source_name,
            "publisher": str(candidate.get("publisher") or ""),
            "candidate_url": candidate_url,
            "source_family": str(candidate.get("source_family") or ""),
            "state": str(candidate.get("state") or ""),
            "location_name": str(candidate.get("location_name") or ""),
            "location_scope": str(candidate.get("location_scope") or ""),
            "candidate_reason": str(candidate.get("candidate_reason") or ""),
            "expected_text_basis": str(candidate.get("expected_text_basis") or ""),
            "extraction_quality_guess": str(candidate.get("extraction_quality_guess") or ""),
            "pressure_topics_expected": candidate.get("pressure_topics_expected") or [],
            "status": str(candidate.get("status") or ""),
            "notes": str(candidate.get("notes") or ""),
            "source_purpose": str(candidate.get("source_purpose") or "unknown"),
            "current_or_evergreen": str(candidate.get("current_or_evergreen") or "unknown"),
            "promotable": bool(candidate.get("promotable", False)),
            "non_promotable_reason": str(candidate.get("non_promotable_reason") or ""),
            "fetched": False,
            "fetch_error": "",
            "item_count": 0,
            "candidate_pressure_item_count": 0,
            "accepted_pressure_item_count": 0,
            "rejected_item_count": 0,
            "noise_score": 100,
            "pressure_hit_rate": 0.0,
            "negative_hit_count": 0,
            "useful_text_available": False,
            "top_pressure_terms": {},
            "sample_accepted_titles": [],
            "sample_rejected_titles": [],
            "recommendation": "skip_quarantined",
            "reason": "quarantined candidate skipped",
            "raw_diagnostics": [],
            "extraction_basis_used": "skipped_quarantined",
            "source_purpose_blocked": False,
            "source_quality_score": int(candidate.get("source_quality_score") or 0),
            "source_quality_tier": str(candidate.get("source_quality_tier") or "quarantine"),
            "first_discovered_at": str(candidate.get("first_discovered_at") or ""),
            "last_discovered_at": str(candidate.get("last_discovered_at") or ""),
            "last_tested_at": "",
            "discovery_count": int(candidate.get("discovery_count") or 0),
            "test_count": int(candidate.get("test_count") or 0),
            "enable_count": int(candidate.get("enable_count") or 0),
            "reject_count": int(candidate.get("reject_count") or 0),
            "keep_candidate_count": int(candidate.get("keep_candidate_count") or 0),
            "last_recommendation": "skip_quarantined",
            "last_recommendation_reason": "quarantined candidate skipped",
            "auto_discovered": bool(candidate.get("auto_discovered", False)),
            "rolling_quality_score": int(performance.get("rolling_quality_score") or 0),
        }
        return {
            "review_row": review_row,
            "audit_row": audit_row,
            "promotion_row": {
                "source_id": source_id,
                "source_name": source_name,
                "previous_status": str(candidate.get("status") or ""),
                "recommendation": "skip_quarantined",
                "promoted": False,
                "reason": "quarantined candidate skipped",
                "target_registry": str(_pressure_registry_path(ROOT)),
                "source_purpose": str(candidate.get("source_purpose") or "unknown"),
            },
            "promote_candidate": False,
            "skipped_quarantined": True,
        }
    candidate_url = str(candidate.get("candidate_url") or "")
    purpose_info = classify_food_line_source_purpose(
        {
            "source_id": candidate.get("source_id"),
            "source_name": candidate.get("source_name"),
            "publisher": candidate.get("publisher"),
            "candidate_url": candidate.get("candidate_url"),
            "source_family": candidate.get("source_family"),
            "source_type": _candidate_source_type(candidate),
            "state": candidate.get("state"),
            "location_name": candidate.get("location_name"),
            "location_scope": candidate.get("location_scope"),
            "candidate_reason": candidate.get("candidate_reason"),
            "expected_text_basis": candidate.get("expected_text_basis"),
            "extraction_quality_guess": candidate.get("extraction_quality_guess"),
            "notes": candidate.get("notes"),
        }
    )
    source_purpose = purpose_info["source_purpose"]
    current_or_evergreen = purpose_info["current_or_evergreen"]
    promotable = purpose_info["promotable"] == "true"
    non_promotable_reason = purpose_info["non_promotable_reason"]
    fetched = False
    fetch_error = ""
    item_details: list[dict[str, Any]] = []
    accepted_titles: list[str] = []
    rejected_titles: list[str] = []
    top_terms: Counter[str] = Counter()
    accepted_pressure_item_count = 0
    candidate_pressure_item_count = 0
    rejected_item_count = 0
    item_count = 0
    useful_text_available = False
    negative_hit_count = 0
    noise_score = 100
    pressure_hit_rate = 0.0
    try:
        payload = fetch(candidate_url, timeout=15)
        items, extraction_basis_used = _parse_candidate_payload(candidate, payload)
        fetched = True
    except Exception as exc:  # noqa: BLE001
        items = []
        extraction_basis_used = "fetch_error"
        fetch_error = f"{type(exc).__name__}: {exc}"
    for item in items[:10]:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip() or candidate_url
        summary = str(item.get("summary_or_snippet") or "").strip()
        evidence_text = str(item.get("evidence_text") or "").strip()
        evidence_text_basis = str(item.get("evidence_text_basis") or "").strip()
        item_page_metadata_date = str(item.get("page_metadata_date") or "").strip()
        if not title and not evidence_text:
            continue
        useful_text_available = True
        item_count += 1
        published_at = str(item.get("published_at") or "").strip()
        date_provenance_warning = _date_provenance_warning(
            published_raw=published_at,
            page_metadata_raw=item_page_metadata_date,
            url_date=_url_path_date(url)[0],
            audit_url_path_date=False,
        )
        pressure_eval = evaluate_food_line_pressure(
            {
                "title": title,
                "summary_or_snippet": summary,
                "url": url,
                "evidence_text": evidence_text,
                "evidence_text_basis": evidence_text_basis,
                "source_family": candidate.get("source_family") or "local_news",
                "source_type": "rss" if extraction_basis_used == "rss" else ("api" if extraction_basis_used == "api" else "page"),
                "state": candidate.get("state") or "US",
                "positive_keywords": candidate.get("pressure_topics_expected") or [],
                "negative_keywords": ["recipe", "restaurant", "menu", "festival", "gala", "cooking"],
                "published_at": published_at,
                "page_metadata_date": item_page_metadata_date,
            },
            edition_date=date,
            pressure_required=True,
            max_age_days=14,
            positive_keywords=candidate.get("pressure_topics_expected") or [],
            negative_keywords=["recipe", "restaurant", "menu", "festival", "gala", "cooking"],
        )
        if pressure_eval.get("rejected"):
            rejected_item_count += 1
            rejected_titles.append(title)
            continue
        if pressure_eval.get("pressure_signal"):
            candidate_pressure_item_count += 1
            top_terms.update(pressure_eval.get("pressure_match_terms") or _pressure_match_terms(evidence_text))
            if str(pressure_eval.get("pressure_verification_status") or "") == "source_text_verified":
                accepted_pressure_item_count += 1
                accepted_titles.append(title)
        else:
            rejected_titles.append(title)
        item_details.append(
            {
                "title": title,
                "url": url,
                "published_at": published_at,
                "page_metadata_date": item_page_metadata_date,
                "pressure_signal": bool(pressure_eval.get("pressure_signal")),
                "pressure_verification_status": pressure_eval.get("pressure_verification_status") or "",
                "pressure_reason": pressure_eval.get("pressure_reason") or "",
                "evidence_text_basis": pressure_eval.get("evidence_text_basis") or "",
                "pressure_match_terms": pressure_eval.get("pressure_match_terms") or [],
                "date_provenance_warning": date_provenance_warning,
            }
        )
    published_at_count = sum(1 for item in item_details if str(item.get("published_at") or "").strip())
    page_metadata_date_count = sum(1 for item in item_details if str(item.get("page_metadata_date") or "").strip())
    date_provenance_warning = next((str(item.get("date_provenance_warning") or "").strip() for item in item_details if str(item.get("date_provenance_warning") or "").strip()), "")
    published_date_basis = "source_published" if published_at_count else ("page_metadata" if page_metadata_date_count else ("retrieved_at_fallback" if item_count else "missing"))
    negative_hit_count = _candidate_negative_hit_count(items)
    noise_score = _candidate_noise_score(item_count, negative_hit_count, rejected_item_count)
    pressure_hit_rate = _candidate_pressure_hit_rate(item_count, candidate_pressure_item_count)
    recommendation, reason = _candidate_recommendation(
        fetched=fetched,
        item_count=item_count,
        accepted_pressure_item_count=accepted_pressure_item_count,
        rejected_item_count=rejected_item_count,
        useful_text_available=useful_text_available,
        noise_score=noise_score,
        pressure_hit_rate=pressure_hit_rate,
        fetch_error=fetch_error,
        top_rejection_reasons=["excluded by negative filter" if rejected_item_count else ""],
        source_status=str(candidate.get("status") or "candidate"),
        candidate_pressure_item_count=candidate_pressure_item_count,
        source_purpose=source_purpose,
        promotable=promotable,
        non_promotable_reason=non_promotable_reason,
    )
    rolling_quality_score = int(performance.get("rolling_quality_score") or 0)
    if int(performance.get("fetch_failures") or 0) >= 3 and recommendation == "enable":
        recommendation = "reject"
        reason = "repeated fetch failures in source performance history"
    elif rolling_quality_score and rolling_quality_score < 20 and recommendation == "enable":
        recommendation = "keep_candidate"
        reason = "low rolling quality score in source performance history"
    source_purpose_blocked = source_purpose in {"donation_page", "evergreen_context", "resource_page", "program_description"}
    return {
        "review_row": {
            "source_id": candidate.get("source_id") or "",
            "source_name": candidate.get("source_name") or "",
            "publisher": candidate.get("publisher") or "",
            "candidate_url": candidate_url,
            "state": candidate.get("state") or "",
            "source_family": candidate.get("source_family") or "",
            "fetched": str(fetched).lower(),
            "fetch_error": fetch_error,
            "item_count": item_count,
            "candidate_pressure_item_count": candidate_pressure_item_count,
            "accepted_pressure_item_count": accepted_pressure_item_count,
            "rejected_item_count": rejected_item_count,
            "noise_score": noise_score,
            "pressure_hit_rate": pressure_hit_rate,
            "negative_hit_count": negative_hit_count,
            "useful_text_available": str(useful_text_available).lower(),
            "source_purpose": source_purpose,
            "current_or_evergreen": current_or_evergreen,
            "promotable": str(promotable).lower(),
            "non_promotable_reason": non_promotable_reason,
            "published_at_count": published_at_count,
            "page_metadata_date_count": page_metadata_date_count,
            "published_date_basis": published_date_basis,
            "date_provenance_warning": date_provenance_warning,
            "source_quality_score": _candidate_quality_score(
                recommendation=recommendation,
                useful_text_available=useful_text_available,
                pressure_hit_rate=pressure_hit_rate,
                noise_score=noise_score,
                fetch_error=fetch_error,
                candidate_pressure_item_count=candidate_pressure_item_count,
                source_purpose=source_purpose,
            ),
            "source_quality_tier": _candidate_quality_tier(
                _candidate_quality_score(
                    recommendation=recommendation,
                    useful_text_available=useful_text_available,
                    pressure_hit_rate=pressure_hit_rate,
                    noise_score=noise_score,
                    fetch_error=fetch_error,
                    candidate_pressure_item_count=candidate_pressure_item_count,
                    source_purpose=source_purpose,
                )
            ),
            "first_discovered_at": str(candidate.get("first_discovered_at") or ""),
            "last_discovered_at": str(candidate.get("last_discovered_at") or ""),
            "last_tested_at": _utc_now(),
            "discovery_count": int(candidate.get("discovery_count") or 0),
            "test_count": int(candidate.get("test_count") or 0) + 1,
            "enable_count": int(candidate.get("enable_count") or 0),
            "reject_count": int(candidate.get("reject_count") or 0),
            "keep_candidate_count": int(candidate.get("keep_candidate_count") or 0),
            "last_recommendation": recommendation,
            "last_recommendation_reason": reason,
            "auto_discovered": bool(candidate.get("auto_discovered", False)),
            "top_pressure_terms": ", ".join(term for term, _count in top_terms.most_common(5)),
            "sample_accepted_titles": " | ".join(accepted_titles[:3]),
            "sample_rejected_titles": " | ".join(rejected_titles[:3]),
            "recommendation": recommendation,
            "reason": reason,
        },
        "audit_row": {
            "source_id": candidate.get("source_id") or "",
            "source_name": candidate.get("source_name") or "",
            "publisher": candidate.get("publisher") or "",
            "candidate_url": candidate_url,
            "source_family": candidate.get("source_family") or "",
            "state": candidate.get("state") or "",
            "location_name": candidate.get("location_name") or "",
            "location_scope": candidate.get("location_scope") or "",
            "candidate_reason": candidate.get("candidate_reason") or "",
            "expected_text_basis": candidate.get("expected_text_basis") or "",
            "extraction_quality_guess": candidate.get("extraction_quality_guess") or "",
            "pressure_topics_expected": candidate.get("pressure_topics_expected") or [],
            "status": candidate.get("status") or "",
            "notes": candidate.get("notes") or "",
            "source_purpose": source_purpose,
            "current_or_evergreen": current_or_evergreen,
            "promotable": promotable,
            "non_promotable_reason": non_promotable_reason,
            "published_at_count": published_at_count,
            "page_metadata_date_count": page_metadata_date_count,
            "published_date_basis": published_date_basis,
            "date_provenance_warning": date_provenance_warning,
            "source_quality_score": _candidate_quality_score(
                recommendation=recommendation,
                useful_text_available=useful_text_available,
                pressure_hit_rate=pressure_hit_rate,
                noise_score=noise_score,
                fetch_error=fetch_error,
                candidate_pressure_item_count=candidate_pressure_item_count,
                source_purpose=source_purpose,
            ),
            "source_quality_tier": _candidate_quality_tier(
                _candidate_quality_score(
                    recommendation=recommendation,
                    useful_text_available=useful_text_available,
                    pressure_hit_rate=pressure_hit_rate,
                    noise_score=noise_score,
                    fetch_error=fetch_error,
                    candidate_pressure_item_count=candidate_pressure_item_count,
                    source_purpose=source_purpose,
                )
            ),
            "first_discovered_at": str(candidate.get("first_discovered_at") or ""),
            "last_discovered_at": str(candidate.get("last_discovered_at") or ""),
            "last_tested_at": _utc_now(),
            "discovery_count": int(candidate.get("discovery_count") or 0),
            "test_count": int(candidate.get("test_count") or 0) + 1,
            "enable_count": int(candidate.get("enable_count") or 0),
            "reject_count": int(candidate.get("reject_count") or 0),
            "keep_candidate_count": int(candidate.get("keep_candidate_count") or 0),
            "last_recommendation": recommendation,
            "last_recommendation_reason": reason,
            "auto_discovered": bool(candidate.get("auto_discovered", False)),
            "fetched": fetched,
            "fetch_error": fetch_error,
            "item_count": item_count,
            "candidate_pressure_item_count": candidate_pressure_item_count,
            "accepted_pressure_item_count": accepted_pressure_item_count,
            "rejected_item_count": rejected_item_count,
            "noise_score": noise_score,
            "pressure_hit_rate": pressure_hit_rate,
            "negative_hit_count": negative_hit_count,
            "useful_text_available": useful_text_available,
            "top_pressure_terms": dict(top_terms.most_common(10)),
            "sample_accepted_titles": accepted_titles[:3],
            "sample_rejected_titles": rejected_titles[:3],
            "recommendation": recommendation,
            "reason": reason,
            "raw_diagnostics": item_details,
            "extraction_basis_used": extraction_basis_used,
            "source_purpose_blocked": source_purpose_blocked,
            "rolling_quality_score": rolling_quality_score,
        },
        "promotion_row": {
            "source_id": candidate.get("source_id") or "",
            "source_name": candidate.get("source_name") or "",
            "previous_status": candidate.get("status") or "",
            "recommendation": recommendation,
            "promoted": False,
            "reason": reason,
            "target_registry": str(_pressure_registry_path(ROOT)),
            "source_purpose": source_purpose,
            "published_at_count": published_at_count,
            "page_metadata_date_count": page_metadata_date_count,
            "published_date_basis": published_date_basis,
            "date_provenance_warning": date_provenance_warning,
        },
        "promote_candidate": recommendation == "enable",
    }


def _parse_candidate_payload(candidate: dict[str, Any], payload: bytes) -> tuple[list[dict[str, Any]], str]:
    basis = str(candidate.get("expected_text_basis") or "manual").strip().lower()
    url = str(candidate.get("candidate_url") or "")
    if basis in {"rss_summary", "rss_title"} or url.endswith((".rss", ".xml")):
        items = _parse_rss_items(payload)
        return items, "rss"
    if basis == "api_json" or url.endswith(".json"):
        data = json.loads(payload.decode("utf-8", errors="replace"))
        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            source_items = data
        elif isinstance(data, dict):
            source_items = data.get("items") or data.get("results") or data.get("entries") or []
        else:
            source_items = []
        for item in source_items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            summary = str(item.get("summary") or item.get("description") or item.get("content") or "").strip()
            link = str(item.get("url") or item.get("link") or item.get("href") or candidate.get("candidate_url") or "").strip()
            items.append(
                {
                    "title": title,
                    "url": link,
                    "published_at": str(item.get("published_at") or item.get("date") or item.get("updated") or "").strip(),
                    "summary_or_snippet": summary,
                    "evidence_text": " ".join(part for part in (title, summary) if part).strip(),
                    "evidence_text_basis": "api_json",
                }
            )
        return items, "api"
    evidence = _extract_page_evidence(payload)
    page_metadata_date = _extract_page_metadata_date(payload)
    return (
        [
            {
                "title": evidence.get("title") or str(candidate.get("source_name") or candidate.get("source_id") or "Candidate source"),
                "url": url,
                "published_at": "",
                "page_metadata_date": page_metadata_date,
                "summary_or_snippet": evidence.get("summary_or_snippet") or "",
                "evidence_text": evidence.get("evidence_text") or "",
                "evidence_text_basis": evidence.get("evidence_text_basis") or "insufficient_evidence",
            }
        ],
        "page",
    )


def test_food_line_candidate_sources(root: Path, date: str, *, fetcher: Any | None = None, promote_enabled: bool = False, include_quarantined: bool = False) -> dict[str, Any]:
    registry_refresh = refresh_food_line_pressure_registry_source_purpose(root)
    candidates = load_food_line_candidate_registry(root)
    fetch = resolve_food_line_fetcher(fetcher)
    review_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    promotion_rows: list[dict[str, Any]] = []
    promoted_candidates: list[dict[str, Any]] = []
    rejected_by_source_purpose_count = 0
    promoted_blocked_by_source_purpose_count = 0
    worker_count = min(16, max(1, len(candidates)))
    def _run(candidate: dict[str, Any]) -> dict[str, Any]:
        return _evaluate_candidate_source(root, candidate, date, fetch, include_quarantined=include_quarantined)
    results = [_run(candidate) for candidate in candidates] if food_line_test_mode_enabled() or worker_count <= 1 else None
    if results is None:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            results = list(executor.map(_run, candidates))
    for result in results:
        review_row = result["review_row"]
        audit_row = result["audit_row"]
        promotion_row = result["promotion_row"]
        review_rows.append(review_row)
        audit_rows.append(audit_row)
        promotion_rows.append(promotion_row)
        if review_row["source_purpose"] in {"donation_page", "evergreen_context", "resource_page", "program_description"}:
            rejected_by_source_purpose_count += 1 if review_row["recommendation"] == "reject" else 0
            if review_row["recommendation"] != "enable":
                promoted_blocked_by_source_purpose_count += 1
        history_quality = int(audit_row.get("rolling_quality_score") or 0)
        history_tests = int(audit_row.get("test_count") or 0)
        review_noise_score = int(review_row.get("noise_score")) if review_row.get("noise_score") is not None else 100
        review_rejected_count = int(review_row.get("rejected_item_count") or 0)
        if result["promote_candidate"] and str(audit_row.get("status") or "").lower() not in {"quarantined", "archived"} and review_noise_score < 75 and review_rejected_count == 0 and not (history_tests >= 3 and history_quality < 20):
            promoted_candidates.append(
                {
                    "source_id": review_row["source_id"],
                    "source_name": review_row["source_name"],
                    "publisher": audit_row["publisher"],
                    "candidate_url": audit_row["candidate_url"],
                    "source_family": audit_row["source_family"],
                    "source_type": _candidate_source_type({"candidate_url": audit_row["candidate_url"], "expected_text_basis": audit_row["expected_text_basis"]}),
                    "state": audit_row["state"],
                    "location_name": audit_row["location_name"],
                    "location_scope": audit_row["location_scope"],
                    "candidate_reason": audit_row["candidate_reason"],
                    "expected_text_basis": audit_row["expected_text_basis"],
                    "extraction_quality_guess": audit_row["extraction_quality_guess"],
                    "pressure_topics_expected": audit_row["pressure_topics_expected"],
                    "status": audit_row["status"],
                    "notes": audit_row["notes"],
                    "source_purpose": audit_row["source_purpose"],
                    "current_or_evergreen": audit_row["current_or_evergreen"],
                    "promotable": audit_row["promotable"],
                    "non_promotable_reason": audit_row["non_promotable_reason"],
                }
            )
    review_path, audit_path = _candidate_review_paths(root, date)
    _write_csv(
        review_path,
        [
            "source_id",
            "source_name",
            "publisher",
            "candidate_url",
            "state",
            "source_family",
            "fetched",
            "fetch_error",
            "item_count",
            "candidate_pressure_item_count",
            "accepted_pressure_item_count",
            "rejected_item_count",
            "noise_score",
            "pressure_hit_rate",
            "negative_hit_count",
            "useful_text_available",
            "source_purpose",
            "current_or_evergreen",
            "promotable",
            "non_promotable_reason",
            "published_at_count",
            "page_metadata_date_count",
            "published_date_basis",
            "date_provenance_warning",
            "source_quality_score",
            "source_quality_tier",
            "first_discovered_at",
            "last_discovered_at",
            "last_tested_at",
            "discovery_count",
            "test_count",
            "enable_count",
            "reject_count",
            "keep_candidate_count",
            "last_recommendation",
            "last_recommendation_reason",
            "auto_discovered",
            "top_pressure_terms",
            "sample_accepted_titles",
            "sample_rejected_titles",
            "recommendation",
            "reason",
        ],
        review_rows,
    )
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit_rows, indent=2), encoding="utf-8")
    promotion_report_path = ""
    promoted_source_ids: list[str] = []
    if promote_enabled:
        if promoted_candidates:
            _upsert_pressure_registry(root, promoted_candidates)
            promoted_source_ids = [str(candidate.get("source_id") or "") for candidate in promoted_candidates if str(candidate.get("source_id") or "").strip()]
            _update_candidate_registry_statuses(root, set(promoted_source_ids))
            for row in promotion_rows:
                if row["source_id"] in promoted_source_ids and row["recommendation"] == "enable":
                    row["promoted"] = True
        promotion_report_path = str(_candidate_promotion_path(root, date))
        _write_csv(
            Path(promotion_report_path),
            [
                "source_id",
                "source_name",
                "previous_status",
                "recommendation",
                "promoted",
                "reason",
                "target_registry",
                "published_at_count",
                "page_metadata_date_count",
                "published_date_basis",
                "date_provenance_warning",
            ],
            promotion_rows,
        )
    promoted_source_id_set = {str(row.get("source_id") or "").strip() for row in promotion_rows if str(row.get("promoted") or "").lower() == "true"}
    candidate_registry_rows = []
    review_by_source_id = {str(row.get("source_id") or ""): row for row in review_rows if str(row.get("source_id") or "").strip()}
    for candidate in candidates:
        source_id = str(candidate.get("source_id") or "").strip()
        updated = dict(candidate)
        previous_status = str(candidate.get("status") or "candidate").lower()
        review_row = review_by_source_id.get(source_id)
        if review_row:
            updated["test_count"] = int(updated.get("test_count") or 0) + 1
            updated["last_tested_at"] = _utc_now()
            updated["last_recommendation"] = str(review_row.get("recommendation") or "")
            updated["last_recommendation_reason"] = str(review_row.get("reason") or "")
            updated["source_quality_score"] = _candidate_quality_score(
                recommendation=str(review_row.get("recommendation") or ""),
                useful_text_available=str(review_row.get("useful_text_available") or "").lower() == "true",
                pressure_hit_rate=float(review_row.get("pressure_hit_rate") or 0),
                noise_score=int(review_row.get("noise_score") or 100),
                fetch_error=str(review_row.get("fetch_error") or ""),
                candidate_pressure_item_count=int(review_row.get("candidate_pressure_item_count") or 0),
                source_purpose=str(review_row.get("source_purpose") or "unknown"),
            )
            updated["source_quality_tier"] = _candidate_quality_tier(int(updated.get("source_quality_score") or 0))
            recommendation = str(review_row.get("recommendation") or "")
            useful_text_available = str(review_row.get("useful_text_available") or "").lower() == "true"
            if recommendation == "enable":
                updated["enable_count"] = int(updated.get("enable_count") or 0) + 1
                if source_id in promoted_source_id_set:
                    updated["status"] = "enabled"
                elif previous_status in {"quarantined", "archived"}:
                    updated["status"] = previous_status
                else:
                    updated["status"] = "tested_good"
            elif recommendation == "reject":
                updated["reject_count"] = int(updated.get("reject_count") or 0) + 1
                if previous_status in {"quarantined", "archived"}:
                    updated["status"] = previous_status
                elif int(updated.get("reject_count") or 0) > 2 or (not useful_text_available and int(updated.get("reject_count") or 0) >= 2):
                    updated["status"] = "quarantined"
                else:
                    updated["status"] = "rejected"
            elif recommendation == "keep_candidate":
                updated["keep_candidate_count"] = int(updated.get("keep_candidate_count") or 0) + 1
                if previous_status in {"quarantined", "archived"}:
                    updated["status"] = previous_status
                elif str(updated.get("status") or "").lower() not in {"enabled", "promoted"}:
                    updated["status"] = "tested_weak" if int(updated.get("keep_candidate_count") or 0) > 1 else "candidate"
            elif recommendation == "skip_quarantined":
                updated["status"] = "quarantined"
            if int(updated.get("source_quality_score") or 0) < 20 and int(updated.get("reject_count") or 0) > 1:
                updated["status"] = "quarantined"
        candidate_registry_rows.append(updated)
    _write_candidate_registry(root, candidate_registry_rows)
    return {
        "ok": True,
        "candidate_count": len(candidates),
        "candidate_review_path": str(review_path),
        "candidate_audit_path": str(audit_path),
        "candidate_promotion_report_path": promotion_report_path,
        "recommendations_by_status": dict(Counter(row["recommendation"] for row in review_rows)),
        "promoted_candidate_count": len(promoted_candidates),
        "promoted_source_ids": promoted_source_ids,
        "rejected_by_source_purpose_count": rejected_by_source_purpose_count,
        "promoted_blocked_by_source_purpose_count": promoted_blocked_by_source_purpose_count,
        "quarantined_skipped_count": sum(1 for row in review_rows if row["recommendation"] == "skip_quarantined"),
        "registry_source_purpose_refresh": registry_refresh,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test candidate Food Line sources")
    parser.add_argument("--date")
    parser.add_argument("--import-intake", help="Import candidate rows from a CSV intake file")
    parser.add_argument("--cleanup-candidates", action="store_true", help="Apply cleanup rules to the candidate registry")
    parser.add_argument("--mode", default="conservative", choices=["conservative", "normal", "aggressive"], help="Cleanup mode")
    parser.add_argument("--dry-run", action="store_true", help="Write cleanup reports without mutating the registry")
    parser.add_argument("--promote-enabled", action="store_true", help="Promote candidates with recommendation=enable into the pressure registry")
    parser.add_argument("--include-quarantined", action="store_true", help="Include quarantined candidates during testing")
    args = parser.parse_args(argv)
    if args.import_intake:
        result = import_food_line_candidate_intake(ROOT, Path(args.import_intake))
        return 0 if result else 1
    if args.cleanup_candidates:
        result = cleanup_food_line_candidates(ROOT, mode=args.mode, dry_run=args.dry_run)
        return 0 if result else 1
    if not args.date:
        parser.error("--date is required unless --import-intake or --cleanup-candidates is provided")
    result = test_food_line_candidate_sources(ROOT, args.date, promote_enabled=args.promote_enabled, include_quarantined=args.include_quarantined)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1

def _source_registry_health_action(
    *,
    row: dict[str, Any],
    history: dict[str, Any],
    latest_review: dict[str, Any],
    mode: str,
) -> tuple[str, str]:
    status = str(row.get("status") or "candidate").lower()
    if status == "enabled":
        return "preserve_enabled", "enabled sources preserved"
    if status in {"quarantined", "archived"}:
        return "keep_candidate", "already finalized"

    fetch_failures = int(history.get("fetch_failures") or 0)
    rejected_count = int(row.get("reject_count") or 0)
    useful_text_available = str(latest_review.get("useful_text_available") or "").lower() == "true"
    fetched = str(latest_review.get("fetched") or "").lower() == "true" or int(history.get("runs_fetched") or 0) > 0
    no_useful_text = not useful_text_available
    repeated_failure = fetch_failures >= 2 or rejected_count >= 2

    if mode == "conservative":
        if fetch_failures >= 3 or (rejected_count >= 3 and no_useful_text):
            return "quarantine", "conservative cleanup: repeated fetch failures or rejects without useful text"
        if repeated_failure or no_useful_text:
            return "quarantine", "conservative cleanup: repeated failure or no useful text"
        return "keep_candidate", "candidate remains under review"

    if (not fetched and (fetch_failures >= 1 or no_useful_text)) or (rejected_count >= 3 and no_useful_text):
        return "archive", "normal/aggressive cleanup: broken or repeated no-text candidate"
    if no_useful_text and repeated_failure:
        return "archive", "normal/aggressive cleanup: repeated no-text failure"
    if repeated_failure or no_useful_text:
        return "quarantine", "normal/aggressive cleanup: repeated failure or no useful text"
    return "keep_candidate", "candidate remains under review"


if __name__ == "__main__":
    raise SystemExit(main())
