from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.adapters.food_line_agent import adapt_food_line_agent_output, map_finding_to_food_line_candidate
from bluefern_dispatches.food_line_current_review import (
    ALLOWED_DECISIONS,
    CURRENT_PRODUCTION_SCOPE,
    HISTORICAL_ROOTS,
    PRIVATE_QUEUE_PATH,
    QUEUE_SCHEMA_VERSION,
    build_proposed_edition,
    load_queue,
    write_json_atomic,
    write_proposed_edition,
)


REPORT_SCHEMA = "food_line_current_intake_report_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process the private Food Line current intake.")
    parser.add_argument("--edition-date", required=True)
    parser.add_argument("--inbox", required=True)
    parser.add_argument("--build-review-queue", action="store_true")
    parser.add_argument("--build-proposed-edition", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _queue_source_paths(root: Path, inbox: Path, edition_date: str) -> list[Path]:
    discovery_candidates = root / "data" / "dispatches" / "food-line" / "discovery" / edition_date / "discovery_candidates.json"
    paths: list[Path] = []
    if inbox.exists():
        paths.extend(
            path
            for path in sorted(inbox.rglob("*.json"))
            if path.is_file() and "processed" not in path.parts
        )
    if not paths and discovery_candidates.exists():
        paths.append(discovery_candidates)
    return paths


def _agent_intake_artifact_path(root: Path, edition_date: str, agent_run_id: str) -> Path:
    safe_run_id = str(agent_run_id or edition_date).strip() or edition_date
    return root / "data" / "dispatches" / "food-line" / "agent-intake" / edition_date / f"{safe_run_id}.json"


def _queue_item_from_candidate(
    candidate: dict[str, Any],
    *,
    edition_date: str,
    source_artifact_path: str,
    proposed_rank: int,
) -> dict[str, Any]:
    row = dict(candidate)
    review_item_id = str(row.get("review_item_id") or row.get("candidate_id") or row.get("agent_finding_id") or row.get("source_record_id") or "").strip()
    source_finding_or_intake_id = str(
        row.get("source_finding_or_intake_id")
        or row.get("agent_finding_id")
        or row.get("candidate_id")
        or review_item_id
    ).strip()
    source_url = str(row.get("source_url") or row.get("canonical_source_url") or row.get("canonical_url") or row.get("url") or "").strip()
    canonical_source_url = str(row.get("canonical_source_url") or row.get("canonical_url") or source_url).strip()
    publisher = str(row.get("publisher") or row.get("source_name") or row.get("source") or "").strip()
    source_published_at = str(row.get("source_published_at") or row.get("published_at") or row.get("source_published_date") or "").strip()
    title = str(row.get("title") or row.get("headline") or row.get("proposed_public_headline") or "").strip()
    exact_supporting_passage = str(row.get("exact_supporting_passage") or row.get("evidence_text") or row.get("summary_or_snippet") or row.get("summary") or "").strip()
    proposed_public_headline = str(
        row.get("proposed_public_headline")
        or row.get("title")
        or row.get("headline")
        or row.get("pressure_summary")
        or row.get("summary_or_snippet")
        or row.get("summary")
        or title
    ).strip()
    proposed_public_summary = str(
        row.get("proposed_public_summary")
        or row.get("pressure_summary")
        or row.get("summary_or_snippet")
        or row.get("summary")
        or row.get("claim_supported")
        or exact_supporting_passage
        or proposed_public_headline
    ).strip()
    location_name = str(row.get("location_name") or row.get("location_scope") or row.get("state") or "United States").strip()
    state = str(row.get("state") or "US").strip().upper() or "US"
    location_scope = str(row.get("location_scope") or "state_local").strip()
    pressure_type = str(row.get("pressure_type") or row.get("map_category") or "food-access pressure").strip()
    affected_groups = row.get("affected_groups")
    if not isinstance(affected_groups, list) or any(not isinstance(value, str) for value in affected_groups):
        affected_groups = []
    why_it_matters = str(row.get("why_it_matters") or row.get("pressure_summary") or proposed_public_summary).strip()
    evidence_level = str(row.get("evidence_level") or "direct_reporting").strip()
    confidence = str(row.get("confidence") or "medium").strip()
    uncertainty_note = str(row.get("uncertainty_note") or row.get("limitations") or "").strip()
    duplicate_check = row.get("duplicate_check") if isinstance(row.get("duplicate_check"), dict) else {"status": "not_published", "matched_records": []}
    freshness_check = row.get("freshness_check") if isinstance(row.get("freshness_check"), dict) else None
    if freshness_check is None:
        source_date = source_published_at[:10]
        try:
            age_days = (datetime.fromisoformat(edition_date) - datetime.fromisoformat(source_date[:10])).days
        except ValueError as exc:
            raise ValueError(f"items[{proposed_rank - 1}].freshness_check could not be derived from source_published_at") from exc
        freshness_check = {"status": "current", "age_days": age_days, "edition_date": edition_date}
    proposed_section = str(
        row.get("proposed_section")
        or ("Core Food Pressure Signals" if bool(row.get("pressure_signal", True)) else "Other Food Line Signals")
    ).strip()
    editorial_status = str(row.get("editorial_status") or ("pending_editorial_review" if bool(row.get("eligible_for_review", True)) else "hold")).strip()
    editorial_note = str(row.get("editorial_note") or ("Pending operator decision." if editorial_status == "pending_editorial_review" else "Held for operator review.")).strip()

    row.update(
        {
            "review_item_id": review_item_id,
            "source_finding_or_intake_id": source_finding_or_intake_id,
            "source_artifact_path": source_artifact_path,
            "source_url": source_url,
            "canonical_source_url": canonical_source_url,
            "publisher": publisher,
            "source_published_at": source_published_at,
            "title": title,
            "exact_supporting_passage": exact_supporting_passage,
            "proposed_public_headline": proposed_public_headline,
            "proposed_public_summary": proposed_public_summary,
            "location_name": location_name,
            "state": state,
            "location_scope": location_scope,
            "pressure_type": pressure_type,
            "affected_groups": affected_groups,
            "why_it_matters": why_it_matters,
            "evidence_level": evidence_level,
            "confidence": confidence,
            "uncertainty_note": uncertainty_note,
            "duplicate_check": duplicate_check,
            "freshness_check": freshness_check,
            "proposed_section": proposed_section,
            "proposed_rank": int(row.get("proposed_rank") or proposed_rank),
            "editorial_status": editorial_status,
            "editorial_note": editorial_note,
            "publication_eligible": False,
        }
    )
    for private_key in ("raw_agent_payload", "private_text_provenance", "chain_of_custody", "hidden_instructions"):
        row.pop(private_key, None)
    return row


def _finding_payload(finding: Any) -> dict[str, Any]:
    if hasattr(finding, "to_dict"):
        payload = finding.to_dict()
        if isinstance(payload, dict):
            return payload
    if isinstance(finding, dict):
        return dict(finding)
    return {"finding_id": str(getattr(finding, "finding_id", ""))}


def _build_review_queue(root: Path, edition_date: str, inbox: Path) -> dict[str, Any]:
    queue_path = root / PRIVATE_QUEUE_PATH
    items: list[dict[str, Any]] = []
    seen_duplicate_keys: set[str] = set()
    for source_index, source_path in enumerate(_queue_source_paths(root, inbox, edition_date), start=1):
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        agent_name = str(payload.get("agent_name") or "Food Line Source Watch") if isinstance(payload, dict) else "Food Line Source Watch"
        agent_run_id = str(payload.get("agent_run_id") or source_path.stem) if isinstance(payload, dict) else source_path.stem
        findings = adapt_food_line_agent_output(
            payload,
            agent_name=agent_name,
            agent_run_id=agent_run_id,
        )
        intake_path = _agent_intake_artifact_path(root, edition_date, agent_run_id)
        intake_rows: list[dict[str, Any]] = []
        intake_artifact = {
            "schema_version": "food_line_agent_intake_v1",
            "agent_name": agent_name,
            "agent_run_id": agent_run_id,
            "started_at": payload.get("started_at") if isinstance(payload, dict) else "",
            "completed_at": payload.get("completed_at") if isinstance(payload, dict) else "",
            "search_window": payload.get("search_window") if isinstance(payload, dict) and isinstance(payload.get("search_window"), dict) else {"edition_date": edition_date},
            "findings": [_finding_payload(finding) for finding in findings],
            "candidate_rows": [],
            "counts": {"eligible_for_review": 0, "excluded": 0},
            "coverage_notes": str(payload.get("coverage_notes") or "Private intake only; review is required before publication.") if isinstance(payload, dict) else "Private intake only; review is required before publication.",
            "input_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "input_filename": source_path.name,
        }
        for finding in findings:
            candidate = map_finding_to_food_line_candidate(finding, edition_date=edition_date)
            row = _queue_item_from_candidate(
                candidate,
                edition_date=edition_date,
                source_artifact_path=intake_path.relative_to(root).as_posix(),
                proposed_rank=len(intake_rows) + 1,
            )
            intake_row = dict(row)
            intake_row["candidate_disposition"] = "reviewable" if bool(intake_row.get("eligible_for_review", True)) else "excluded"
            if intake_row["candidate_disposition"] == "excluded":
                intake_row["candidate_disposition_reason"] = str(
                    intake_row.get("exclusion_reason")
                    or intake_row.get("rejection_reason")
                    or intake_row.get("editorial_note")
                    or "not eligible for review"
                ).strip()
            intake_rows.append(intake_row)
            duplicate_key = str(row.get("agent_duplicate_key") or row.get("candidate_id") or "")
            if duplicate_key and duplicate_key in seen_duplicate_keys:
                intake_rows[-1]["candidate_disposition"] = "duplicate"
                intake_rows[-1]["candidate_disposition_reason"] = "duplicate agent_duplicate_key within intake"
                intake_rows[-1]["eligible_for_review"] = False
                intake_rows[-1]["editorial_status"] = "reject"
                intake_rows[-1]["editorial_note"] = (
                    "Duplicate Food Line Source Watch finding retained for audit but excluded from the review queue."
                )
                continue
            if duplicate_key:
                seen_duplicate_keys.add(duplicate_key)
            if not bool(row.get("eligible_for_review", True)):
                continue
            items.append(row)
        intake_artifact["candidate_rows"] = intake_rows
        intake_artifact["counts"] = {
            "eligible_for_review": sum(1 for row in intake_rows if bool(row.get("eligible_for_review", True))),
            "excluded": sum(1 for row in intake_rows if str(row.get("candidate_disposition") or "") == "excluded"),
            "duplicate": sum(1 for row in intake_rows if str(row.get("candidate_disposition") or "") == "duplicate"),
        }
        write_json_atomic(intake_path, intake_artifact)
    queue = {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "queue_id": f"food-line-current-review-{edition_date}",
        "edition_date": edition_date,
        "production_scope": CURRENT_PRODUCTION_SCOPE,
        "historical_roots_excluded": list(HISTORICAL_ROOTS),
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "items": sorted(
            items,
            key=lambda item: (int(item.get("proposed_rank") or 0), str(item.get("review_item_id") or "")),
        ),
    }
    write_json_atomic(queue_path, queue)
    return queue


def _current_intake_report(root: Path, edition_date: str, inbox: Path) -> dict[str, Any]:
    queue_path = root / "data" / "dispatches" / "food-line" / "review" / "current-signal-review.json"
    queue = load_queue(queue_path)
    proposed = build_proposed_edition(queue)
    json_path = markdown_path = None
    if proposed.get("selected_item_count") is not None:
        json_path, markdown_path, proposed = write_proposed_edition(root, queue)
    inbox_count = len([path for path in inbox.rglob("*") if path.is_file()]) if inbox.exists() else 0
    queue_item_count = len(queue.get("items") or [])
    approved_count = int(proposed.get("approved_item_count") or 0)
    pending_count = int(proposed.get("pending_item_count") or 0)
    rejected_count = int(proposed.get("rejected_item_count") or 0)
    status = "success_with_exclusions" if rejected_count else "success"
    return {
        "schema_version": REPORT_SCHEMA,
        "created_at": _utc_now(),
        "edition_date": edition_date,
        "inbox": str(inbox),
        "discovered_file_count": inbox_count or queue_item_count,
        "accepted_file_count": queue_item_count,
        "import_count": queue_item_count,
        "dry_run_count": 0,
        "import_attempt_count": queue_item_count,
        "idempotent_noop_count": 0,
        "errors": [],
        "status": status,
        "queue": {
            "status": "written",
            "item_count": queue_item_count,
            "approved_item_count": approved_count,
            "pending_item_count": pending_count,
            "rejected_item_count": rejected_count,
            "path": str(queue_path),
        },
        "proposal": {
            "status": "written",
            "draft_status": proposed.get("draft_status"),
            "json_path": str(json_path) if json_path else None,
            "markdown_path": str(markdown_path) if markdown_path else None,
        },
        "publication_side_effects": {
            "public_output": False,
            "pages": False,
            "bluesky": False,
            "audio": False,
            "maps": False,
            "schedule": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path.cwd()
    inbox = Path(args.inbox)
    try:
        queue_path = root / "data" / "dispatches" / "food-line" / "review" / "current-signal-review.json"
        if args.build_review_queue or not queue_path.exists():
            _build_review_queue(root, args.edition_date, inbox)
        report = _current_intake_report(root, args.edition_date, inbox)
        if not args.dry_run:
            report_path = root / "data" / "dispatches" / "food-line" / "review" / "reports" / args.edition_date / "current-intake.json"
            write_json_atomic(report_path, report)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as exc:
        report = {
            "schema_version": REPORT_SCHEMA,
            "created_at": _utc_now(),
            "edition_date": args.edition_date,
            "status": "failed",
            "errors": [str(exc)],
            "publication_side_effects": {
                "public_output": False,
                "pages": False,
                "bluesky": False,
                "audio": False,
                "maps": False,
                "schedule": False,
            },
        }
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
