import hashlib
import json
from pathlib import Path

import scripts.import_historical_agent_runs as cli
from scripts.import_historical_agent_runs import batch_id_for, discover_batch_files, main


def finding(url: str, **extra) -> dict:
    value = {
        "title": "Historical pressure",
        "publisher": "Example",
        "source_url": url,
        "source_published_at": "2026-07-20",
        "exact_supporting_passage": "Food bank demand increased as households lost benefits.",
        "summary": "Historical pressure signal.",
        "location_name": "Example County",
        "state": "NC",
    }
    value.update(extra)
    return value


def envelope(*rows: dict) -> dict:
    return {
        "schema_version": "historical_fixture_v1",
        "agent_name": "fixture",
        "agent_run_id": "run-1",
        "started_at": "2026-07-20T00:00:00Z",
        "findings": list(rows),
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def run_json(capsys, argv: list[str]) -> tuple[int, dict]:
    code = main(argv)
    return code, json.loads(capsys.readouterr().out)


def test_batch_order_ids_and_ignored_files_are_deterministic(tmp_path: Path):
    input_dir = tmp_path / "staging"
    input_dir.mkdir()
    (input_dir / "B.md").write_text("B", encoding="utf-8")
    (input_dir / "a.txt").write_text("A", encoding="utf-8")
    (input_dir / ".hidden.json").write_text("{}", encoding="utf-8")
    (input_dir / "work.tmp").write_text("temp", encoding="utf-8")
    (input_dir / "image.png").write_bytes(b"png")
    write_json(input_dir / "corrections" / "sidecar.json", {"raw_sha256": "x"})
    write_json(input_dir / "nested" / "c.json", {})
    first = discover_batch_files(input_dir)
    second = discover_batch_files(input_dir)
    assert [path.name for path in first] == ["a.txt", "B.md"]
    assert first == second
    assert [path.relative_to(input_dir).as_posix() for path in discover_batch_files(input_dir, recursive=True)] == ["a.txt", "B.md", "nested/c.json"]
    assert batch_id_for("care-line", first) == batch_id_for("care-line", second)
    assert batch_id_for("food-line", first) != batch_id_for("care-line", first)


def test_batch_validate_and_dry_run_write_nothing(tmp_path: Path, capsys):
    input_dir = tmp_path / "staging"
    write_json(input_dir / "one.json", envelope(finding("https://example.com/one")))
    for operation in ("batch-validate", "batch-dry-run"):
        code, result = run_json(capsys, [operation, "--domain", "food-line", "--input-dir", str(input_dir), "--repo-root", str(tmp_path)])
        assert code == 0
        assert result["file_count"] == 1
        assert result["files"][0]["validation_status"] == "valid"
        assert not (tmp_path / "data/agent-history").exists()


def test_default_batch_import_blocks_all_and_partial_import_isolated(tmp_path: Path, capsys):
    input_dir = tmp_path / "staging"
    write_json(input_dir / "good.json", envelope(finding("https://example.com/good")))
    (input_dir / "bad.md").write_text("```json\n{}\n```\n```json\n{}\n```", encoding="utf-8")
    code, blocked = run_json(capsys, ["batch-import", "--domain", "food-line", "--input-dir", str(input_dir), "--repo-root", str(tmp_path)])
    assert code == 1
    assert blocked["status"] == "blocked_validation"
    assert not list((tmp_path / "data/agent-history/food-line/raw").glob("*.json"))
    code, partial = run_json(capsys, ["batch-import", "--domain", "food-line", "--input-dir", str(input_dir), "--repo-root", str(tmp_path), "--allow-partial-import"])
    assert code == 1
    assert partial["status"] == "partial_completed"
    assert partial["imported_files"] == 1
    assert partial["invalid_files"] == 1
    assert len(list((tmp_path / "data/agent-history/food-line/raw").glob("*.json"))) == 1


def test_batch_rerun_is_idempotent_and_report_counts_are_correct(tmp_path: Path, capsys):
    input_dir = tmp_path / "staging"
    write_json(input_dir / "one.json", envelope(finding("https://example.com/one")))
    first_code, first = run_json(capsys, ["batch-import", "--domain", "food-line", "--input-dir", str(input_dir), "--repo-root", str(tmp_path)])
    second_code, second = run_json(capsys, ["batch-import", "--domain", "food-line", "--input-dir", str(input_dir), "--repo-root", str(tmp_path)])
    assert first_code == second_code == 0
    assert first["batch_id"] == second["batch_id"]
    assert first["imported_files"] == 1
    assert second["idempotent_files"] == 1
    report = json.loads(Path(second["report_path"]).read_text(encoding="utf-8"))
    assert report["total_files"] == 1
    assert report["publication_ready_count"] == 0


def care_sidecar(input_dir: Path, raw_path: Path, *, duplicate: bool = False) -> None:
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    sidecar = {
        "raw_sha256": digest,
        "raw_file": raw_path.relative_to(input_dir.parents[2]).as_posix(),
        "domain": "care-line",
        "normalization_type": "prose_envelope_to_structured_findings",
        "reviewer": "fixture",
        "reviewed_at": "2026-07-29T00:00:00Z",
        "approved": True,
        "approval_scope": "historical_normalization_only",
        "publication_approval": False,
        "findings": [{
            "finding_id": "care-1",
            "source_url": "https://example.com/care",
            "canonical_source_url": "https://example.com/care",
            "source_published_at": "2026-07-29",
            "event_type": "permanent_service_closure",
            "access_direction": "access_loss",
            "exact_supporting_passage": "The clinic permanently closed.",
            "review_status": "pending_review",
            "publication_approval": False,
        }],
    }
    write_json(input_dir / "corrections" / "sidecar.json", sidecar)
    if duplicate:
        write_json(input_dir / "corrections" / "other-name.json", sidecar)


def test_sidecars_match_by_hash_and_multiple_matches_fail_closed(tmp_path: Path, capsys):
    input_dir = tmp_path / "data/agent-history-staging/care-line"
    input_dir.mkdir(parents=True)
    raw_path = input_dir / "not-the-sidecar-name.txt"
    raw_path.write_text("https://example.com/care\nThe clinic permanently closed.", encoding="utf-8")
    care_sidecar(input_dir, raw_path)
    code, result = run_json(capsys, ["batch-dry-run", "--domain", "care-line", "--input-dir", str(input_dir), "--repo-root", str(tmp_path)])
    assert code == 0
    assert result["files"][0]["matching_sidecar"] == "corrections/sidecar.json"
    care_sidecar(input_dir, raw_path, duplicate=True)
    code, result = run_json(capsys, ["batch-dry-run", "--domain", "care-line", "--input-dir", str(input_dir), "--repo-root", str(tmp_path)])
    assert code == 1
    assert "multiple sidecars" in result["files"][0]["error"]


def test_sidecar_declared_run_identity_mismatch_fails_closed(tmp_path: Path, capsys):
    input_dir = tmp_path / "staging"
    raw_path = input_dir / "alert.json"
    write_json(raw_path, envelope(finding("https://example.com/story")))
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    write_json(input_dir / "corrections" / "identity.json", {
        "raw_sha256": digest,
        "finding_identity": {
            "agent_run_id": "different-run",
            "source_url": "https://example.com/story",
        },
    })
    code, result = run_json(capsys, ["batch-dry-run", "--domain", "food-line", "--input-dir", str(input_dir), "--repo-root", str(tmp_path)])
    assert code == 1
    assert "agent_run_id mismatch" in result["files"][0]["error"]


def test_sidecar_declared_raw_file_hash_mismatch_fails_closed(tmp_path: Path, capsys):
    input_dir = tmp_path / "data/agent-history-staging/food-line"
    raw_path = input_dir / "alert.json"
    write_json(raw_path, envelope(finding("https://example.com/story")))
    write_json(input_dir / "corrections" / "wrong-hash.json", {
        "raw_sha256": "0" * 64,
        "raw_file": raw_path.relative_to(tmp_path).as_posix(),
        "domain": "food-line",
    })
    code, result = run_json(capsys, ["batch-dry-run", "--domain", "food-line", "--input-dir", str(input_dir), "--repo-root", str(tmp_path)])
    assert code == 1
    assert "raw SHA-256 mismatch" in result["files"][0]["error"]


def test_cross_domain_batch_safety(tmp_path: Path, capsys):
    care_dir = tmp_path / "care"
    published_id = "event-published"
    write_json(tmp_path / "data/universal_events/publication-state/care-line-signal-wire.json", {"events": {published_id: {}}})
    write_json(care_dir / "care.json", {"findings": [{"event_id": published_id, "source_url": "https://example.com/care", "event_date": "2026-07-20", "evidence": "Hospital access changed."}]})
    _, care = run_json(capsys, ["batch-dry-run", "--domain", "care-line", "--input-dir", str(care_dir), "--repo-root", str(tmp_path)])
    assert care["files"][0]["outcomes"] == {"matched_published_event": 1}
    assert not (tmp_path / "data/universal_events/publication-state/care-line-reviewed-event-queue.json").exists()

    food_dir = tmp_path / "food"
    write_json(food_dir / "food.json", envelope(finding("https://example.com/food", exact_supporting_passage="General background only")))
    _, food = run_json(capsys, ["batch-dry-run", "--domain", "food-line", "--input-dir", str(food_dir), "--repo-root", str(tmp_path)])
    assert food["archived_invalid_findings"] == 1
    assert food["candidates_created"] == 0

    gaza_dir = tmp_path / "gaza"
    write_json(tmp_path / "data/dispatches/gaza/sources/existing.json", {"url": "https://example.com/gaza"})
    write_json(gaza_dir / "gaza.json", {"findings": [{"source_url": "https://example.com/gaza", "event_date": "2026-07-20", "evidence": "Historical report."}]})
    _, gaza = run_json(capsys, ["batch-dry-run", "--domain", "gaza", "--input-dir", str(gaza_dir), "--repo-root", str(tmp_path)])
    assert gaza["files"][0]["outcomes"] == {"matched_existing": 1}

    ice_dir = tmp_path / "ice"
    write_json(ice_dir / "ice.json", {"findings": [{"source_url": "https://example.com/ice", "event_date": "2026-07-20", "evidence": "Private record."}]})
    _, ice = run_json(capsys, ["batch-import", "--domain", "ice", "--input-dir", str(ice_dir), "--repo-root", str(tmp_path)])
    assert ice["imported_files"] == 1
    assert not (tmp_path / "output/site").exists()


def test_one_failed_atomic_file_write_does_not_corrupt_other_imports(tmp_path: Path, capsys, monkeypatch):
    input_dir = tmp_path / "staging"
    write_json(input_dir / "a.json", envelope(finding("https://example.com/a")))
    write_json(input_dir / "b.json", envelope(finding("https://example.com/b")))
    failed_digest = hashlib.sha256((input_dir / "a.json").read_bytes()).hexdigest()
    real_atomic = cli.atomic_json

    def fail_one(path: Path, value: object) -> None:
        if path.name == f"{failed_digest}.json" and path.parent.name == "raw":
            raise OSError("synthetic interrupted write")
        real_atomic(path, value)

    monkeypatch.setattr(cli, "atomic_json", fail_one)
    code, result = run_json(capsys, ["batch-import", "--domain", "food-line", "--input-dir", str(input_dir), "--repo-root", str(tmp_path)])
    assert code == 1
    assert result["status"] == "partial_failed"
    assert result["failed_files"] == 1
    assert result["imported_files"] == 1
    assert not (tmp_path / "data/agent-history/food-line/raw" / f"{failed_digest}.json").exists()
