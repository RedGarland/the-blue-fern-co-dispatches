from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

import scripts.run_food_line_daily_ops as daily_ops


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _install_successful_mocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    order: list[str],
    *,
    discovery_enabled: bool = True,
    pressure_verified_count: int = 1,
    candidate_count: int = 12,
    promoted_candidate_count: int = 2,
    compliance_ok: bool = True,
    audio_generated: bool = False,
    audio_available: bool | None = None,
    audio_reused_existing: bool | None = None,
    audio_required: bool = False,
    force_audio_regenerate: bool = False,
    tts_exception_type: str | None = None,
    tts_exception_message_sanitized: str | None = None,
    audio_timeout_seconds: float = 90.0,
) -> None:
    def cleanup(root: Path, *, mode: str = "conservative", dry_run: bool = False):
        order.append(f"cleanup:{mode}:{dry_run}")
        path = root / "output" / "review" / "food-line" / "candidate_cleanup_report.csv"
        health = root / "output" / "review" / "food-line" / "source_registry_health_report.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("source_id,new_status\ncandidate-a,archived\n", encoding="utf-8")
        health.write_text("source_id,status\ncandidate-a,archived\n", encoding="utf-8")
        return {
            "ok": True,
            "candidate_count_before": 10,
            "candidate_count_after": 10,
            "quarantined_count": 1,
            "archived_count": 6,
            "preserved_enabled_count": 2,
            "dry_run": dry_run,
            "mode": mode,
            "cleanup_report_path": str(path),
            "source_registry_health_report_path": str(health),
            "registry_path": str(root / "data" / "dispatches" / "food-line" / "candidate_source_registry.json"),
        }

    def discovery(root: Path, date: str, **kwargs):
        if not discovery_enabled:
            raise AssertionError("discovery should not run in this test")
        order.append("discovery")
        review_path = root / "output" / "review" / "food-line" / date / "source_discovery_review.csv"
        audit_path = root / "data" / "dispatches" / "food-line" / "sources" / date / "source_discovery_audit.json"
        query_report_path = root / "output" / "review" / "food-line" / "discovery_query_performance_report.csv"
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text("source_id,action\nseed-a,inserted_candidate\n", encoding="utf-8")
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text("[]", encoding="utf-8")
        query_report_path.write_text("query_template,rolling_query_quality_score\nq,10\n", encoding="utf-8")
        return {
            "ok": True,
            "inserted_count": 25,
            "updated_count": 225,
            "review_path": str(review_path),
            "audit_path": str(audit_path),
            "query_performance_report_path": str(query_report_path),
            "source_registry_health_report_path": str(root / "output" / "review" / "food-line" / "source_registry_health_report.csv"),
        }

    def candidate_test(root: Path, date: str, *, promote_enabled: bool = False, include_quarantined: bool = False):
        order.append(f"candidate:{promote_enabled}")
        review_path = root / "output" / "review" / "food-line" / date / "candidate_source_review.csv"
        audit_path = root / "data" / "dispatches" / "food-line" / "sources" / date / "candidate_source_audit.json"
        promotion_path = root / "output" / "review" / "food-line" / date / "candidate_promotion_report.csv"
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text("source_id,recommendation,pressure_signal\ncandidate-a,enable,true\n", encoding="utf-8")
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text("[]", encoding="utf-8")
        promotion_path.write_text("source_id,source_name,previous_status,recommendation,promoted,reason,target_registry\n", encoding="utf-8")
        return {
            "ok": True,
            "candidate_count": candidate_count,
            "candidate_review_path": str(review_path),
            "candidate_audit_path": str(audit_path),
            "candidate_promotion_report_path": str(promotion_path),
            "promoted_candidate_count": promoted_candidate_count,
            "promoted_source_ids": ["candidate-a"],
        }

    def production(root: Path, date: str, *, collect: bool = False, **kwargs):
        order.append(f"production:{collect}")
        pressure_review_path = root / "output" / "review" / "food-line" / date / "pressure_review.csv"
        collector_audit_path = root / "data" / "dispatches" / "food-line" / "sources" / date / "collector_audit.json"
        pressure_review_path.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(
            pressure_review_path,
            ["source_record_id", "pressure_signal", "pressure_verification_status", "source_title", "location_name", "pressure_type", "pressure_summary", "evidence_text"],
            [
                {
                    "source_record_id": "food-line-auto-1",
                    "pressure_signal": "true",
                    "pressure_verification_status": "source_text_verified",
                    "source_title": "KLTV",
                    "location_name": "East Texas, TX",
                    "pressure_type": "demand strain",
                    "pressure_summary": "KLTV reported rising food-assistance demand across its service area.",
                    "evidence_text": "food banks across Texas are working hard to keep up with rising demand",
                }
            ]
            if pressure_verified_count
            else [],
        )
        collector_audit_path.parent.mkdir(parents=True, exist_ok=True)
        collector_audit_path.write_text("[]", encoding="utf-8")
        return {
            "ok": True,
            "lead_source_record_id": "food-line-auto-1",
            "selected_lead_pressure_type": "demand strain",
            "selected_lead_affected_groups": ["SNAP households"],
            "pressure_verified_count": pressure_verified_count,
            "pressure_marker_count": pressure_verified_count,
            "pressure_review_path": str(pressure_review_path),
            "collector_audit_path": str(collector_audit_path),
            "audio_generated": audio_generated,
            "audio_available": audio_generated if audio_available is None else audio_available,
            "audio_reused_existing": ((audio_generated if audio_available is None else audio_available) and not audio_generated) if audio_reused_existing is None else audio_reused_existing,
            "audio_required": audio_required,
            "audio_mp3_path": str(root / "output" / "site" / "food-line" / "audio" / f"{date}.mp3") if audio_generated else None,
            "audio_mp3_url": f"/food-line/audio/{date}.mp3" if audio_generated else None,
            "podcast_enclosure_present": audio_generated if audio_available is None else audio_available,
            "existing_audio_mp3_path": str(root / "output" / "site" / "food-line" / "audio" / f"{date}.mp3") if (audio_available if audio_available is not None else audio_generated) else None,
            "existing_audio_mp3_size": 13 if (audio_available if audio_available is not None else audio_generated) else None,
            "force_audio_regenerate": force_audio_regenerate,
            "audio_temp_path": str(root / "output" / "site" / "food-line" / "audio" / f"{date}.tmp.mp3") if audio_generated or force_audio_regenerate else None,
            "audio_replacement_performed": bool(audio_generated and (audio_available if audio_available is not None else audio_generated) and force_audio_regenerate),
            "audio_status": "audio_file_ready" if audio_generated else "transcript_only",
            "audio_timeout_seconds": audio_timeout_seconds,
            "tts_provider": "openai" if audio_generated or audio_required else "none",
            "tts_model_requested": "gpt-4o-mini-tts" if audio_generated or audio_required else None,
            "tts_voice_requested": "alloy" if audio_generated or audio_required else None,
            "tts_narration_char_count": 123 if audio_generated or audio_required else None,
            "tts_output_path_attempted": str(root / "output" / "site" / "food-line" / "audio" / f"{date}.mp3") if audio_generated or audio_required else None,
            "tts_api_key_present": bool(audio_generated or audio_required),
            "tts_output_dir_exists": True if audio_generated or audio_required else None,
            "tts_partial_mp3_exists": audio_generated,
            "tts_elapsed_seconds": 0.05 if audio_generated or audio_required else None,
            "tts_exception_type": tts_exception_type,
            "tts_exception_message_sanitized": tts_exception_message_sanitized,
            "tts_timeout_seconds": audio_timeout_seconds,
            "tts_audio_format": "mp3" if audio_generated or audio_required else None,
            "tls_verify": True if audio_generated or audio_required else None,
            "ca_file_used": None,
            "ca_source": "system_default" if audio_generated or audio_required else None,
            "truststore_requested": False if audio_generated or audio_required else None,
            "truststore_available": False if audio_generated or audio_required else None,
            "ssl_cert_file_env": None,
            "requests_ca_bundle_env": None,
            "bluefern_tts_ca_file_env": None,
            "tls_workaround_warning": None,
            "tts_file_write_exception_type": None,
            "tts_file_write_exception_message_sanitized": None,
            "warnings": [],
        }

    def publish(root: Path, date: str):
        order.append("publish")
        return True, [], {"ok": True}

    def push():
        order.append("push")
        return True, "pushed"

    def compliance(root: Path, date: str):
        order.append("compliance")
        return {
            "ok": compliance_ok,
            "report_json": str(root / "output" / "review" / "food-line" / date / "blue_fern_compliance_report.json"),
            "report_md": str(root / "output" / "review" / "food-line" / date / "blue_fern_compliance_report.md"),
            "warnings": [] if compliance_ok else ["blue fern compliance failed"],
            "failures": [] if compliance_ok else ["blue fern compliance failed"],
        }

    monkeypatch.setattr(daily_ops, "cleanup_food_line_candidates", cleanup)
    monkeypatch.setattr(daily_ops, "discover_food_line_sources", discovery)
    monkeypatch.setattr(daily_ops, "test_food_line_candidate_sources", candidate_test)
    monkeypatch.setattr(daily_ops, "run_food_line_dispatch", production)
    monkeypatch.setattr(daily_ops, "publish_food_line_pages", publish)
    monkeypatch.setattr(daily_ops, "push_pages_repo", push)
    monkeypatch.setattr(daily_ops, "run_food_line_blue_fern_compliance", compliance)
    monkeypatch.setattr(daily_ops, "ROOT", tmp_path)


def test_food_line_daily_ops_defaults_date_to_today(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    order: list[str] = []
    monkeypatch.setattr(daily_ops, "_today_local_date", lambda: "2026-06-03")
    _install_successful_mocks(monkeypatch, tmp_path, order)

    exit_code = daily_ops.main(["--publish"])
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert exit_code == 0
    assert payload["date"] == "2026-06-03"
    assert payload["ok"] is True
    assert order == ["cleanup:conservative:False", "discovery", "candidate:True", "production:True", "publish"]


def test_food_line_daily_ops_runs_workflow_and_writes_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    order: list[str] = []
    _install_successful_mocks(monkeypatch, tmp_path, order)

    exit_code = daily_ops.main(["--date", "2026-06-13", "--publish", "--push"])
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    log_path = tmp_path / "logs" / "food-line" / "daily_ops" / "2026-06-13.log"
    status_path = tmp_path / "status" / "food-line-daily-ops-status.json"
    assert exit_code == 0
    assert order == ["cleanup:conservative:False", "discovery", "candidate:True", "production:True", "publish", "push"]
    assert payload["ok"] is True
    assert payload["date"] == "2026-06-13"
    assert payload["cleanup_mode"] == "conservative"
    assert payload["published"] is True
    assert payload["pushed"] is True
    assert payload["pressure_verified_count"] == 1
    assert payload["pressure_marker_count"] == 1
    assert "blue_fern_compliance_ok" in payload
    assert "blue_fern_compliance_report_json" in payload
    assert "blue_fern_compliance_report_md" in payload
    assert Path(payload["daily_ops_report_json"]).exists()
    assert Path(payload["daily_ops_report_md"]).exists()
    assert log_path.exists()
    assert status_path.exists()
    log_text = log_path.read_text(encoding="utf-8")
    assert "Command arguments:" in log_text
    assert "Substep start: cleanup" in log_text
    assert "Substep end: production" in log_text
    assert "Final JSON summary:" in log_text
    status_payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert status_payload["last_run_date"] == "2026-06-13"
    assert status_payload["ok"] is True
    assert status_payload["latest_log_path"] == str(log_path)


def test_food_line_daily_ops_records_audio_fields_when_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    order: list[str] = []
    _install_successful_mocks(monkeypatch, tmp_path, order, audio_generated=True, audio_required=True)

    result = daily_ops.run_food_line_daily_ops(
        tmp_path,
        "2026-06-13",
        publish=True,
        check_blue_fern_compliance=True,
        generate_audio=True,
        require_audio=True,
    )

    assert result["ok"] is True
    assert result["audio_generated"] is True
    assert result["audio_required"] is True
    assert result["audio_mp3_path"].endswith("2026-06-13.mp3")
    assert result["audio_mp3_url"] == "/food-line/audio/2026-06-13.mp3"
    assert result["podcast_enclosure_present"] is True
    assert result["tls_verify"] is True
    assert result["ca_source"] == "system_default"
    assert result["truststore_requested"] is False
    assert result["blue_fern_compliance_ok"] is True


def test_food_line_daily_ops_reuses_existing_audio_when_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    order: list[str] = []
    _install_successful_mocks(
        monkeypatch,
        tmp_path,
        order,
        audio_generated=False,
        audio_available=True,
        audio_reused_existing=True,
        audio_required=True,
    )

    result = daily_ops.run_food_line_daily_ops(
        tmp_path,
        "2026-06-13",
        publish=True,
        check_blue_fern_compliance=True,
        generate_audio=True,
        require_audio=True,
    )

    assert result["ok"] is True
    assert result["audio_generated"] is False
    assert result["audio_available"] is True
    assert result["audio_reused_existing"] is True
    assert result["podcast_enclosure_present"] is True


def test_food_line_daily_ops_require_audio_failure_blocks_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    order: list[str] = []
    _install_successful_mocks(monkeypatch, tmp_path, order, audio_generated=False, audio_available=False, audio_reused_existing=False, audio_required=True)

    exit_code = daily_ops.main(["--date", "2026-06-13", "--publish", "--check-blue-fern-compliance", "--no-discovery-safe-mode", "--require-audio"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["production_ok"] is False
    assert payload["published"] is False
    assert payload["audio_generated"] is False
    assert payload["audio_required"] is True
    assert payload["pushed"] is False
    assert any("require-audio" in warning.lower() or "audio narration was not generated" in warning.lower() for warning in payload["warnings"])


def test_food_line_daily_ops_push_requires_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr(daily_ops, "ROOT", tmp_path)
    monkeypatch.setattr(daily_ops, "_today_local_date", lambda: "2026-06-03")

    exit_code = daily_ops.main(["--push"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert payload["ok"] is False
    assert payload["reason"] == "--push requires --publish"
    assert (tmp_path / "logs" / "food-line" / "daily_ops" / "2026-06-03.log").exists()
    status_path = tmp_path / "status" / "food-line-daily-ops-status.json"
    assert status_path.exists()
    status_payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert status_payload["ok"] is False


def test_food_line_daily_ops_lock_prevents_overlapping_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr(daily_ops, "ROOT", tmp_path)
    monkeypatch.setattr(daily_ops, "_today_local_date", lambda: "2026-06-03")
    lock_path = tmp_path / "runtime" / "food-line-daily-ops.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": os.getpid(), "created_at": "2026-06-03T00:00:00"}), encoding="utf-8")

    monkeypatch.setattr(daily_ops, "cleanup_food_line_candidates", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cleanup should not run")))
    monkeypatch.setattr(daily_ops, "discover_food_line_sources", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("discovery should not run")))
    monkeypatch.setattr(daily_ops, "test_food_line_candidate_sources", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("candidate should not run")))
    monkeypatch.setattr(daily_ops, "run_food_line_dispatch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("production should not run")))

    exit_code = daily_ops.main(["--publish"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["ok"] is False
    assert "already running" in payload["reason"].lower()
    assert lock_path.exists()
    status_payload = json.loads((tmp_path / "status" / "food-line-daily-ops-status.json").read_text(encoding="utf-8"))
    assert status_payload["ok"] is False
    assert any("already running" in warning.lower() for warning in status_payload["warnings"])


def test_food_line_daily_ops_stale_lock_is_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    order: list[str] = []
    monkeypatch.setattr(daily_ops, "_today_local_date", lambda: "2026-06-03")
    _install_successful_mocks(monkeypatch, tmp_path, order)
    lock_path = tmp_path / "runtime" / "food-line-daily-ops.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": 999999, "created_at": "2026-06-01T00:00:00"}), encoding="utf-8")
    monkeypatch.setattr(daily_ops, "_acquire_lock", lambda path: (True, None))

    exit_code = daily_ops.main(["--publish"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert not lock_path.exists()


def test_food_line_daily_ops_production_failure_prevents_push(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    monkeypatch.setattr(daily_ops, "cleanup_food_line_candidates", lambda *args, **kwargs: {"ok": True, "candidate_count_before": 1, "candidate_count_after": 1, "quarantined_count": 0, "archived_count": 0, "preserved_enabled_count": 0, "cleanup_report_path": str(tmp_path / "cleanup.csv"), "source_registry_health_report_path": str(tmp_path / "health.csv"), "registry_path": str(tmp_path / "candidate.json")})
    monkeypatch.setattr(daily_ops, "discover_food_line_sources", lambda *args, **kwargs: {"ok": True, "inserted_count": 0, "updated_count": 0, "review_path": str(tmp_path / "discover.csv"), "audit_path": str(tmp_path / "discover.json"), "query_performance_report_path": str(tmp_path / "query.csv"), "source_registry_health_report_path": str(tmp_path / "health.csv")})
    monkeypatch.setattr(daily_ops, "test_food_line_candidate_sources", lambda *args, **kwargs: {"ok": True, "candidate_count": 1, "candidate_review_path": str(tmp_path / "candidate.csv"), "candidate_audit_path": str(tmp_path / "candidate.json"), "candidate_promotion_report_path": str(tmp_path / "promotion.csv"), "promoted_candidate_count": 0, "promoted_source_ids": []})

    def production(*args, **kwargs):
        calls.append("production")
        return {"ok": False, "pressure_verified_count": 0, "pressure_marker_count": 0, "pressure_review_path": str(tmp_path / "pressure.csv"), "collector_audit_path": str(tmp_path / "collector.json")}

    def publish(*args, **kwargs):
        calls.append("publish")
        return True, [], {"ok": True}

    def push():
        calls.append("push")
        return True, "pushed"

    monkeypatch.setattr(daily_ops, "run_food_line_dispatch", production)
    monkeypatch.setattr(daily_ops, "publish_food_line_pages", publish)
    monkeypatch.setattr(daily_ops, "push_pages_repo", push)

    result = daily_ops.run_food_line_daily_ops(tmp_path, "2026-06-13", publish=True, push=True)
    assert result["production_ok"] is False
    assert result["published"] is False
    assert result["pushed"] is False
    assert calls == ["production"]
    assert any("production run failed" in warning.lower() for warning in result["warnings"])


def test_food_line_daily_ops_compliance_failure_blocks_push_but_still_allows_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    order: list[str] = []
    _install_successful_mocks(monkeypatch, tmp_path, order, compliance_ok=False)

    exit_code = daily_ops.main(["--date", "2026-06-13", "--publish", "--push", "--check-blue-fern-compliance"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["published"] is True
    assert payload["pushed"] is False
    assert payload["blue_fern_compliance_ok"] is False
    assert payload["blue_fern_compliance_report_json"].endswith("blue_fern_compliance_report.json")
    assert payload["blue_fern_compliance_report_md"].endswith("blue_fern_compliance_report.md")
    assert order == ["cleanup:conservative:False", "discovery", "candidate:True", "production:True", "compliance", "publish"]


def test_food_line_daily_ops_zero_pressure_warns_but_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(daily_ops, "cleanup_food_line_candidates", lambda *args, **kwargs: {"ok": True, "candidate_count_before": 1, "candidate_count_after": 1, "quarantined_count": 0, "archived_count": 0, "preserved_enabled_count": 0, "cleanup_report_path": str(tmp_path / "cleanup.csv"), "source_registry_health_report_path": str(tmp_path / "health.csv"), "registry_path": str(tmp_path / "candidate.json")})
    monkeypatch.setattr(daily_ops, "discover_food_line_sources", lambda *args, **kwargs: {"ok": True, "inserted_count": 0, "updated_count": 0, "review_path": str(tmp_path / "discover.csv"), "audit_path": str(tmp_path / "discover.json"), "query_performance_report_path": str(tmp_path / "query.csv"), "source_registry_health_report_path": str(tmp_path / "health.csv")})
    monkeypatch.setattr(daily_ops, "test_food_line_candidate_sources", lambda *args, **kwargs: {"ok": True, "candidate_count": 1, "candidate_review_path": str(tmp_path / "candidate.csv"), "candidate_audit_path": str(tmp_path / "candidate.json"), "candidate_promotion_report_path": str(tmp_path / "promotion.csv"), "promoted_candidate_count": 0, "promoted_source_ids": []})

    pressure_review_path = tmp_path / "output" / "review" / "food-line" / "2026-06-13" / "pressure_review.csv"
    pressure_review_path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(
        pressure_review_path,
        ["source_record_id", "pressure_signal", "pressure_verification_status", "source_title", "location_name", "pressure_type", "pressure_summary", "evidence_text"],
        [],
    )

    def production(*args, **kwargs):
        return {
            "ok": True,
            "pressure_verified_count": 0,
            "pressure_marker_count": 0,
            "pressure_review_path": str(pressure_review_path),
            "collector_audit_path": str(tmp_path / "collector.json"),
        }

    monkeypatch.setattr(daily_ops, "run_food_line_dispatch", production)
    monkeypatch.setattr(daily_ops, "publish_food_line_pages", lambda *args, **kwargs: (True, [], {"ok": True}))
    monkeypatch.setattr(daily_ops, "push_pages_repo", lambda: (True, "pushed"))

    result = daily_ops.run_food_line_daily_ops(tmp_path, "2026-06-13")
    assert result["ok"] is True
    assert result["published"] is False
    assert any("no verified pressure records found" in warning.lower() for warning in result["warnings"])


def test_food_line_daily_ops_no_discovery_safe_mode_skips_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    order: list[str] = []
    monkeypatch.setattr(daily_ops, "_today_local_date", lambda: "2026-06-03")
    _install_successful_mocks(monkeypatch, tmp_path, order, discovery_enabled=False)

    exit_code = daily_ops.main(["--publish", "--no-discovery-safe-mode"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert order == ["cleanup:conservative:False", "candidate:True", "production:True", "publish"]


def test_food_line_daily_ops_max_runtime_emits_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    monkeypatch.setattr(daily_ops, "cleanup_food_line_candidates", lambda *args, **kwargs: calls.append("cleanup") or {"ok": True, "candidate_count_before": 1, "candidate_count_after": 1, "quarantined_count": 0, "archived_count": 0, "preserved_enabled_count": 0, "cleanup_report_path": str(tmp_path / "cleanup.csv"), "source_registry_health_report_path": str(tmp_path / "health.csv"), "registry_path": str(tmp_path / "candidate.json")})
    monkeypatch.setattr(daily_ops, "discover_food_line_sources", lambda *args, **kwargs: calls.append("discovery") or {"ok": True, "inserted_count": 0, "updated_count": 0, "review_path": str(tmp_path / "discover.csv"), "audit_path": str(tmp_path / "discover.json"), "query_performance_report_path": str(tmp_path / "query.csv"), "source_registry_health_report_path": str(tmp_path / "health.csv")})
    monkeypatch.setattr(daily_ops, "test_food_line_candidate_sources", lambda *args, **kwargs: calls.append("candidate") or {"ok": True, "candidate_count": 1, "candidate_review_path": str(tmp_path / "candidate.csv"), "candidate_audit_path": str(tmp_path / "candidate.json"), "candidate_promotion_report_path": str(tmp_path / "promotion.csv"), "promoted_candidate_count": 0, "promoted_source_ids": []})
    monkeypatch.setattr(daily_ops, "run_food_line_dispatch", lambda *args, **kwargs: calls.append("production") or {"ok": True, "pressure_verified_count": 0, "pressure_marker_count": 0, "pressure_review_path": str(tmp_path / "pressure.csv"), "collector_audit_path": str(tmp_path / "collector.json")})
    monkeypatch.setattr(daily_ops, "publish_food_line_pages", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("publish should not run")))
    monkeypatch.setattr(daily_ops, "push_pages_repo", lambda: (_ for _ in ()).throw(AssertionError("push should not run")))

    result = daily_ops.run_food_line_daily_ops(tmp_path, "2026-06-13", max_runtime_minutes=0)
    assert result["ok"] is False
    assert result["stopped_due_runtime_limit"] is True
    assert calls == []
    assert any("max runtime" in warning.lower() for warning in result["warnings"])


def test_food_line_daily_ops_exit_code_for_safety_violation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr(daily_ops, "ROOT", tmp_path)
    monkeypatch.setattr(daily_ops, "_today_local_date", lambda: "2026-06-03")

    exit_code = daily_ops.main(["--push"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert payload["ok"] is False
    assert payload["reason"] == "--push requires --publish"
