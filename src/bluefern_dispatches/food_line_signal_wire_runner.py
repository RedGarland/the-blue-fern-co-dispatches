from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bluefern_dispatches.food_line_discovery_expansion import run_food_line_discovery_expansion
from bluefern_dispatches.food_line_signal_wire import build_signal_wire_event_from_candidate
from bluefern_dispatches.food_line_signal_wire_preview import build_food_line_signal_wire_preview, write_food_line_signal_wire_preview


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _pacific_date() -> str:
    return datetime.now(timezone.utc).astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()


@dataclass(frozen=True)
class SignalWirePaths:
    root: Path

    @property
    def state_root(self) -> Path:
        return self.root / "status" / "food-line" / "signal-wire"

    @property
    def lock_dir(self) -> Path:
        return self.root / "status" / "food-line" / "locks" / "signal-wire.lock"

    def run_dir(self, day: str, run_id: str) -> Path:
        return self.root / "data" / "dispatches" / "food-line" / "signal-wire" / "runs" / day / run_id

    def dry_run_dir(self, day: str, run_id: str) -> Path:
        return self.root / "output" / "review" / "food-line" / "signal-wire" / "live-dry-run" / run_id

    def publication_state(self) -> Path:
        return self.root / "data" / "dispatches" / "food-line" / "signal-wire" / "publication-state.json"


@contextmanager
def _signal_wire_lock(paths: SignalWirePaths):
    paths.lock_dir.parent.mkdir(parents=True, exist_ok=True)
    if paths.lock_dir.exists():
        raise RuntimeError("signal-wire lock already exists")
    if (paths.root / "status" / "food-line" / "locks" / "source-watch.lock").exists():
        raise RuntimeError("source-watch lock already exists")
    paths.lock_dir.mkdir()
    try:
        yield
    finally:
        try:
            paths.lock_dir.rmdir()
        except OSError:
            pass


def _classify_candidate(candidate: dict[str, Any], event: dict[str, Any]) -> str:
    if bool(event.get("wire_auto_publish_eligible")):
        return "eligible_new"
    if str(candidate.get("material_update_requires_review") or "").strip():
        return "material_update_requires_review"
    if str(candidate.get("duplicate_of") or "").strip():
        return "eligible_unchanged_duplicate"
    return "ineligible"


def run_signal_wire_intraday(
    root: Path,
    *,
    dry_run: bool = True,
    check_only: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    paths = SignalWirePaths(root)
    if check_only:
        return {
            "ok": True,
            "status": "success",
            "dry_run": False,
            "check_only": True,
            "run_id": run_id or f"signal-wire-check-{_pacific_date()}",
            "candidate_count": 0,
            "qualified_count": 0,
            "eligible_new_count": 0,
        }
    day = _pacific_date()
    run_id = run_id or f"signal-wire-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    with _signal_wire_lock(paths):
        discovery = run_food_line_discovery_expansion(
            root,
            day,
            edition_mode="current_update",
            max_results_per_query=5,
            max_queries=4,
            query_lookback_days=1,
            query_lookahead_days=0,
            public_claim_lookback_days=0,
            public_claim_lookahead_days=0,
            dry_run=True,
        )
        candidates = [row for row in discovery.get("candidates") or discovery.get("_candidate_records") or [] if isinstance(row, dict)]
        events = []
        for candidate in candidates:
            if not bool(candidate.get("public_claim_eligible")):
                continue
            event = build_signal_wire_event_from_candidate(candidate, as_of=day)
            events.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "signal_id": event["signal_id"],
                    "classification": _classify_candidate(candidate, event),
                    "headline": event["headline"],
                    "summary": event["public_summary"],
                    "eligible": bool(event["wire_auto_publish_eligible"]),
                    "eligibility_reason": event["wire_auto_publish_reason"],
                    "bluesky_post_text": event["bluesky_post_text"],
                    "bluesky_text_length": event["bluesky_text_length"],
                    "public_permalink": event["public_permalink"],
                    "card_image_path": event["card_image_path"],
                    "publisher": event["publisher"],
                    "state": event["state"],
                    "pressure_category": event["pressure_category"],
                }
            )
        run_dir = paths.dry_run_dir(day, run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "ok": True,
            "status": "success",
            "run_id": run_id,
            "started_at": _utc_now(),
            "completed_at": _utc_now(),
            "scan_started_at": discovery.get("generated_at") or _utc_now(),
            "scan_completed_at": _utc_now(),
            "source_count": len(discovery.get("query_rows") or []),
            "candidate_count": len(candidates),
            "qualified_count": sum(1 for c in candidates if bool(c.get("public_claim_eligible"))),
            "eligible_new_count": sum(1 for e in events if e["classification"] == "eligible_new"),
            "duplicate_count": sum(1 for e in events if e["classification"] == "eligible_unchanged_duplicate"),
            "ineligible_count": sum(1 for e in events if e["classification"] == "ineligible"),
            "events": events,
            "discovery": {
                k: discovery.get(k)
                for k in (
                    "ok",
                    "discovery_candidate_count",
                    "discovery_qualified_candidate_count",
                    "discovery_context_candidate_count",
                    "discovery_blocked_candidate_count",
                    "discovery_confidence",
                    "discovery_confidence_reason",
                )
            },
            "paths": {
                "run_dir": str(run_dir),
                "publication_state": str(paths.publication_state()),
            },
            "would_publish_page": any(e["eligible"] for e in events),
            "would_post_bluesky": any(e["eligible"] for e in events),
        }
        (run_dir / "run.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (run_dir / "events.json").write_text(json.dumps(events, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        preview = build_food_line_signal_wire_preview(root)
        write_food_line_signal_wire_preview(root)
        payload["preview_count"] = len(preview["examples"])
        return payload
