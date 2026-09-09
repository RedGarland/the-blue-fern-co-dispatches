from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bluefern_dispatches import ice_monitor
from bluefern_dispatches.ice_dispatch import event_to_dict, normalize_ice_candidate

NOW = "2026-09-09T16:00:00Z"


def source(url: str = "https://official.example/ice/a", *, publisher: str = "Official Source", tier: int = 1) -> dict:
    return {
        "source_url": url,
        "publisher": publisher,
        "source_type": "official",
        "tier": tier,
        "published_at": "2026-09-09T12:00:00Z",
        "modified_at": None,
        "date_source": "official_publication_date",
        "date_confidence": "high",
        "retrieved_at": NOW,
        "exact_supporting_passage": "Official source reports an ICE enforcement operation.",
    }


def event(**overrides) -> dict:
    record = {
        "event_date": "2026-09-09",
        "event_type": "ICE enforcement operation in Los Angeles",
        "primary_category": "enforcement",
        "severity": "medium",
        "status": "reported",
        "location": {"state_or_territory": "CA", "city": "Los Angeles", "location_precision": "city", "geography_source": "source_text", "geography_provenance": "Los Angeles"},
        "impact": {"arrests_count": 10},
        "agencies": {"ice": True},
        "sources": [source()],
        "verification_status": "source_traceable",
        "corroboration_count": 1,
        "currentness_status": "current_publication",
        "currentness_confidence": "high",
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(record.get(key), dict):
            merged = dict(record[key])
            merged.update(value)
            record[key] = merged
        else:
            record[key] = value
    return event_to_dict(normalize_ice_candidate(record, observed_at=NOW))


def diagnostic(events: list[dict], *, health: str = "healthy") -> dict:
    return {
        "run_manifest": {
            "run_id": "diagnostic-run",
            "configured_providers": 2,
            "attempted_providers": 2,
            "successful_providers": 2 if health != "collection_failed" else 0,
            "failed_providers": 0 if health != "collection_failed" else 2,
            "accepted_records": len(events),
            "publisher_count": 1,
            "source_tier_diversity": [1],
            "geography_coverage": ["CA"],
            "category_coverage": ["enforcement"],
            "shared_service_failures": [],
            "provider_health": [{"source_id": "official", "attempted": True, "success": health != "collection_failed"}],
            "health": health,
        },
        "raw_candidates": events,
        "normalized_events": events,
        "canonical_events": events,
        "event_relationships": [],
        "map_readiness": [],
        "exclusions": [],
        "fetch_results": [],
        "publication_decision": "eligible_for_editorial_review" if events else "no_publication_needed",
        "public_side_effects": False,
    }


def install_diagnostic(monkeypatch: pytest.MonkeyPatch, runs: list[dict]) -> None:
    iterator = iter(runs)
    monkeypatch.setattr(ice_monitor, "run_diagnostic", lambda *args, **kwargs: next(iterator))


def run_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[dict], *, run_id: str = "run-1", observed_at: str = NOW):
    install_diagnostic(monkeypatch, [diagnostic(events)])
    return ice_monitor.run_monitor(tmp_path, registry=Path("registry.yml"), run_id=run_id, observed_at=observed_at, live=False)


def test_monitor_only_mode_writes_durable_queue_and_no_public_side_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, payload = run_once(tmp_path, monkeypatch, [event()])

    assert code == 0
    assert payload["mode"] == "MONITOR_ONLY"
    assert payload["publication_side_effects"] is False
    assert payload["public_artifacts_written"] is False
    assert payload["audio_requested"] is False
    assert payload["bluesky_requested"] is False
    assert Path(payload["review_queue_path"]).is_file()
    queue = json.loads(Path(payload["review_queue_path"]).read_text(encoding="utf-8"))
    assert len(queue["items"]) == 1
    assert queue["items"][0]["review_status"] == "NEW"
    assert (Path(payload["run_dir"]) / "operator_summary.json").is_file()


def test_no_new_events_is_successful_no_new_reviewable_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = event()
    install_diagnostic(monkeypatch, [diagnostic([first]), diagnostic([first])])

    ice_monitor.run_monitor(tmp_path, registry=Path("registry.yml"), run_id="run-1", observed_at="2026-09-09T16:00:00Z", live=False)
    code, payload = ice_monitor.run_monitor(tmp_path, registry=Path("registry.yml"), run_id="run-2", observed_at="2026-09-10T16:00:00Z", live=False)

    assert code == 0
    assert payload["operator_summary"]["new_events_since_prior_run"] == 0
    assert payload["operator_summary"]["duplicates"] == 1
    assert json.loads(Path(payload["review_queue_path"]).read_text(encoding="utf-8"))["items"][0]["last_seen"] == "2026-09-10T16:00:00Z"


def test_new_source_for_same_event_accumulates_and_does_not_reenter_new(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = event()
    second = event(sources=[source("https://second.example/ice/a", publisher="Second Source")])
    install_diagnostic(monkeypatch, [diagnostic([first]), diagnostic([second])])

    ice_monitor.run_monitor(tmp_path, registry=Path("registry.yml"), run_id="run-1", observed_at="2026-09-09T16:00:00Z", live=False)
    _, payload = ice_monitor.run_monitor(tmp_path, registry=Path("registry.yml"), run_id="run-2", observed_at="2026-09-10T16:00:00Z", live=False)

    queue = json.loads(Path(payload["review_queue_path"]).read_text(encoding="utf-8"))
    assert len(queue["items"]) == 1
    assert len(queue["items"][0]["source_set"]) == 2
    assert queue["items"][0]["review_status"] == "DUPLICATE_UPDATED"
    assert payload["operator_summary"]["updates"] == 1


def test_updated_arrest_count_and_correction_preserve_canonical_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = event(impact={"arrests_count": 10})
    updated = event(impact={"arrests_count": 12})
    corrected = event(impact={"arrests_count": 12}, supersedes=["prior-fact"])
    install_diagnostic(monkeypatch, [diagnostic([first]), diagnostic([updated]), diagnostic([corrected])])

    ice_monitor.run_monitor(tmp_path, registry=Path("registry.yml"), run_id="run-1", observed_at="2026-09-09T16:00:00Z", live=False)
    _, update_payload = ice_monitor.run_monitor(tmp_path, registry=Path("registry.yml"), run_id="run-2", observed_at="2026-09-10T16:00:00Z", live=False)
    _, correction_payload = ice_monitor.run_monitor(tmp_path, registry=Path("registry.yml"), run_id="run-3", observed_at="2026-09-11T16:00:00Z", live=False)

    assert update_payload["operator_summary"]["updates"] == 1
    assert correction_payload["operator_summary"]["corrections"] == 1
    state = json.loads(Path(correction_payload["state_path"]).read_text(encoding="utf-8"))
    assert len(state["events"]) == 1


def test_tier3_hold_can_transition_to_corroborated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tier3 = event(sources=[source("https://advocacy.example/ice/a", publisher="Advocacy", tier=3)])
    official = event(sources=[source("https://official.example/ice/a", publisher="Official", tier=1)])
    install_diagnostic(monkeypatch, [diagnostic([tier3]), diagnostic([official])])

    _, first_payload = ice_monitor.run_monitor(tmp_path, registry=Path("registry.yml"), run_id="run-1", observed_at="2026-09-09T16:00:00Z", live=False)
    first_queue = json.loads(Path(first_payload["review_queue_path"]).read_text(encoding="utf-8"))
    _, second_payload = ice_monitor.run_monitor(tmp_path, registry=Path("registry.yml"), run_id="run-2", observed_at="2026-09-10T16:00:00Z", live=False)
    second_queue = json.loads(Path(second_payload["review_queue_path"]).read_text(encoding="utf-8"))

    assert first_queue["items"][0]["review_status"] == "NEEDS_CORROBORATION"
    assert second_queue["items"][0]["corroboration_status"] == "corroborated"


def test_stale_event_ages_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, payload = run_once(tmp_path, monkeypatch, [event()], observed_at="2026-09-01T16:00:00Z")
    assert code == 0
    install_diagnostic(monkeypatch, [diagnostic([])])
    ice_monitor.run_monitor(tmp_path, registry=Path("registry.yml"), run_id="run-2", observed_at="2026-09-10T16:00:00Z", live=False, stale_after_days=7)

    queue = json.loads(Path(payload["review_queue_path"]).read_text(encoding="utf-8"))
    assert queue["items"][0]["review_status"] == "REJECTED"
    assert queue["items"][0]["stale_aged_out"] is True


def test_distinct_same_city_incidents_remain_distinct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = event(event_type="ICE enforcement operation in Los Angeles", impact={"arrests_count": 10})
    second = event(event_type="ICE detention facility oversight lawsuit in Los Angeles", primary_category="legal_oversight_accountability")
    _, payload = run_once(tmp_path, monkeypatch, [first, second])
    queue = json.loads(Path(payload["review_queue_path"]).read_text(encoding="utf-8"))
    assert len(queue["items"]) == 2


def test_degraded_run_exits_zero_but_collection_failed_is_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_diagnostic(monkeypatch, [diagnostic([], health="collection_degraded"), diagnostic([], health="collection_failed")])
    degraded_code, degraded_payload = ice_monitor.run_monitor(tmp_path, registry=Path("registry.yml"), run_id="degraded", observed_at="2026-09-09T16:00:00Z", live=False)
    failed_code, failed_payload = ice_monitor.run_monitor(tmp_path, registry=Path("registry.yml"), run_id="failed", observed_at="2026-09-09T17:00:00Z", live=False)

    assert degraded_code == 0
    assert degraded_payload["operator_summary"]["collection_health"] == "collection_degraded"
    assert failed_code == 2
    assert failed_payload["status"] == "collection_failed"


def test_runtime_state_preservation_across_code_sync_simulation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, payload = run_once(tmp_path, monkeypatch, [event()])
    state_before = Path(payload["state_path"]).read_text(encoding="utf-8")
    queue_before = Path(payload["review_queue_path"]).read_text(encoding="utf-8")

    install_diagnostic(monkeypatch, [diagnostic([])])
    ice_monitor.run_monitor(tmp_path, registry=Path("registry.yml"), run_id="run-2", observed_at="2026-09-10T16:00:00Z", live=False)

    assert json.loads(Path(payload["state_path"]).read_text(encoding="utf-8"))["events"]
    assert json.loads(Path(payload["review_queue_path"]).read_text(encoding="utf-8"))["items"]
    assert state_before != ""
    assert queue_before != ""


def test_production_preflight_rejects_dirty_or_wrong_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = iter([
        subprocess.CompletedProcess([], 0, " M unsafe.txt\n", ""),
        subprocess.CompletedProcess([], 0, "", ""),
        subprocess.CompletedProcess([], 0, "wrong\n", ""),
    ])
    monkeypatch.setattr(ice_monitor, "_run", lambda *args, **kwargs: next(calls))

    with pytest.raises(RuntimeError, match="unexpected dirty paths"):
        ice_monitor.verify_monitor_checkout(tmp_path)
    with pytest.raises(RuntimeError, match="branch mismatch"):
        ice_monitor.verify_monitor_checkout(tmp_path)


def test_monitor_cli_success_and_child_failure_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps([event()]), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_ice_monitor.py",
            "--repo-root",
            str(tmp_path),
            "--registry",
            str(Path(__file__).resolve().parents[1] / "data" / "dispatches" / "ice" / "sources.yml"),
            "--fixture",
            str(fixture),
            "--no-live",
            "--run-id",
            "cli-run",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["mode"] == "MONITOR_ONLY"

    monkeypatch.setattr(ice_monitor, "run_diagnostic", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        ice_monitor.run_monitor(tmp_path, registry=Path("registry.yml"), run_id="boom", live=False)


def test_scheduler_scripts_contain_no_publication_flags() -> None:
    root = Path(__file__).resolve().parents[1]
    wrapper = (root / "scripts" / "windows" / "run_ice_monitor.ps1").read_text(encoding="utf-8")
    register = (root / "scripts" / "windows" / "register_ice_monitor_task.ps1").read_text(encoding="utf-8")
    combined = f"{wrapper}\n{register}"
    assert "run_ice_monitor.py" in wrapper
    assert "--enforce-production-preflight" in wrapper
    assert "run_ice_staging_publication" not in combined
    assert "run_ice_edition_candidate" not in combined
    assert "--publish" not in combined
    assert "--post-bluesky" not in combined
    assert "audio" not in combined.lower()
    assert "WorkingDirectory" in register
    assert "-WindowHours 168" in register
    assert "IgnoreNew" in register


def test_bounded_storage_artifacts_exclude_http_bodies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, payload = run_once(tmp_path, monkeypatch, [event()])
    run_dir = Path(payload["run_dir"])
    names = {path.name for path in run_dir.iterdir()}
    assert {"raw_candidates.json", "normalized_events.json", "canonical_events.json", "exclusions.json", "operator_summary.json", "monitor_receipt.json"} <= names
    assert not any("body" in path.name.lower() or "html" in path.suffix.lower() for path in run_dir.iterdir())
    assert sum(path.stat().st_size for path in run_dir.rglob("*") if path.is_file()) < 250_000
