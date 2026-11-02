from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import run_gaza_production_adapter as adapter  # noqa: E402


HEAD = "0" * 40
RECORD_PATH = "data/records/records.json"


def _make_source(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    pages = tmp_path / "pages"
    pages.mkdir()
    for relative in adapter.EXPLICIT_SCRIPT_FILES:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n", encoding="utf-8")
    operational_health = source / "src" / "bluefern_dispatches" / "operational_health.py"
    operational_health.parent.mkdir(parents=True, exist_ok=True)
    operational_health.write_text("# health\n", encoding="utf-8")
    for index, relative in enumerate(adapter.SHARED_DISPATCH_RECORD_PATHS, start=1):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([{"record_id": f"seed-{index}", "history": [index]}]) + "\n", encoding="utf-8")
    return source, pages


def _config(tmp_path: Path, source: Path, pages: Path) -> adapter.ProductionGazaConfig:
    manifest = adapter.build_source_manifest(source)
    return adapter.ProductionGazaConfig(
        source_root=source,
        pages_repo=pages,
        receipt_root=tmp_path / "receipts",
        artifact_root=tmp_path / "artifacts",
        scheduled_for="2026-11-02",
        source_branch="add/pages-repo-default",
        pages_branch="gh-pages",
        python_executable=Path(sys.executable),
        expected_source_head=HEAD,
        expected_source_manifest_identity=manifest["identity"],
    )


def _request(config: adapter.ProductionGazaConfig) -> adapter.RunRequest:
    manifest = adapter.build_source_manifest(config.source_root)
    identity = adapter.source_identity(config.source_root, manifest, lambda _source: HEAD)
    return adapter.RunRequest(
        "gaza",
        "gaza-record-compatibility-test",
        config.scheduled_for,
        config.identity(),
        "input-fingerprint",
        source_identity=identity,
    )


def _clean(_source: Path) -> str:
    return ""


def _runner(
    config: adapter.ProductionGazaConfig,
    *,
    mutate_records: bool = False,
    calls: list[int] | None = None,
    operator_status: str = "NO_UPDATE_LOCAL_PUBLISH_READY",
):
    receipt_path = config.source_root / "status" / "operational-health" / "gaza" / config.scheduled_for / "runs" / "native.json"

    def run(_args, _cwd):
        if calls is not None:
            calls.append(1)
        if mutate_records:
            record_path = config.source_root / RECORD_PATH
            history = json.loads(record_path.read_text(encoding="utf-8"))
            history.append({"record_id": "generated", "history": [99]})
            record_path.write_text(json.dumps(history) + "\n", encoding="utf-8")
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(
                {
                    "schema_version": "bluefern_operational_health_receipt_v1",
                    "dispatch": "gaza",
                    "task_key": "gaza_daily_dispatch",
                    "scheduled_for": config.scheduled_for,
                    "runner_path": str(config.source_root),
                    "source_head": HEAD,
                    "classification": operator_status.lower(),
                    "exit_code": 0,
                    "status": "SAFE_NO_OP",
                    "run_id": "native-record-compatibility",
                    "public_side_effects": {"pages": False, "bluesky": False, "audio": False, "public_output": False},
                    "details": {"generation_ok": True, "validation_ok": True},
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        payload = {
            "operator_status": operator_status,
            "ok": True,
            "date": config.scheduled_for,
            "generation_ok": True,
            "validation_ok": True,
            "operational_receipt": str(receipt_path),
        }
        return adapter.CommandResult(0, json.dumps(payload) + "\n", "", "2026-11-02T00:00:00Z", "2026-11-02T00:01:00Z")

    return run


def test_known_shared_records_are_preserved_and_bound(tmp_path: Path) -> None:
    source, pages = _make_source(tmp_path)
    before = {relative: (source / relative).read_bytes() for relative in adapter.SHARED_DISPATCH_RECORD_PATHS}
    config = _config(tmp_path, source, pages)
    calls: list[int] = []
    result = adapter.run_scheduled_gaza_contract(
        config,
        runner=_runner(config, mutate_records=True, calls=calls),
        head_reader=lambda _source: HEAD,
        status_reader=lambda _source: " M data/records/records.json\n",
    )
    assert result["success"] is True
    assert calls == [1]
    after = {relative: (source / relative).read_bytes() for relative in adapter.SHARED_DISPATCH_RECORD_PATHS}
    assert all(after[relative] for relative in adapter.SHARED_DISPATCH_RECORD_PATHS)
    assert json.loads(after[RECORD_PATH])[:-1] == json.loads(before[RECORD_PATH])
    assert len(json.loads(after[RECORD_PATH])) == len(json.loads(before[RECORD_PATH])) + 1
    for relative in adapter.SHARED_DISPATCH_RECORD_PATHS:
        if relative != RECORD_PATH:
            assert after[relative] == before[relative]
    collection = result["phases"]["collection"]
    binding = collection["summary"]["shared_dispatch_record_binding"]
    assert binding["before"]["files"][RECORD_PATH]["sha256"] != binding["after"]["files"][RECORD_PATH]["sha256"]
    assert any(item["artifact_id"] == "gaza-shared-dispatch-record-binding" for item in collection["artifacts"])


@pytest.mark.parametrize(
    "status",
    (
        " M scripts/run_daily_gaza.py\n",
        " M data/records/detail_packages.json\n",
        "?? data/records/records.json\n",
        "M data/records/records.json\n",
        " MXdata/records/records.json\n",
        "## add/pages-repo-default\n",
        " M data\\records\\records.json\n",
        "bad\n",
    ),
)
def test_unapproved_or_malformed_status_blocks_before_operator(tmp_path: Path, status: str) -> None:
    source, pages = _make_source(tmp_path)
    config = _config(tmp_path, source, pages)
    calls: list[int] = []
    result = adapter.ProductionGazaAdapter(
        config,
        runner=_runner(config, calls=calls),
        head_reader=lambda _source: HEAD,
        status_reader=lambda _source: status,
    ).collect(_request(config))
    assert result.status == "blocked"
    assert calls == []


def test_reparse_record_path_blocks_before_operator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, pages = _make_source(tmp_path)
    record_path = source / RECORD_PATH
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: str(path) == str(record_path) or original(path))
    config = _config(tmp_path, source, pages)
    calls: list[int] = []
    result = adapter.ProductionGazaAdapter(
        config,
        runner=_runner(config, calls=calls),
        head_reader=lambda _source: HEAD,
        status_reader=lambda _source: " M data/records/records.json\n",
    ).collect(_request(config))
    assert result.status == "blocked"
    assert calls == []


def test_reparse_record_ancestor_blocks_before_operator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, pages = _make_source(tmp_path)
    record_parent = source / "data" / "records"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: str(path) == str(record_parent) or original(path))
    config = _config(tmp_path, source, pages)
    calls: list[int] = []
    result = adapter.ProductionGazaAdapter(
        config,
        runner=_runner(config, calls=calls),
        head_reader=lambda _source: HEAD,
        status_reader=lambda _source: " M data/records/records.json\n",
    ).collect(_request(config))
    assert result.status == "blocked"
    assert calls == []


def test_record_history_drift_fails_validation_without_recollection(tmp_path: Path) -> None:
    source, pages = _make_source(tmp_path)
    record_path = source / RECORD_PATH
    config = _config(tmp_path, source, pages)
    calls: list[int] = []
    kwargs = {
        "head_reader": lambda _source: HEAD,
        "status_reader": lambda _source: " M data/records/records.json\n",
    }
    result = adapter.run_scheduled_gaza_contract(
        config,
        runner=_runner(config, calls=calls, operator_status="SAFE_PREPARATION_READY"),
        **kwargs,
    )
    assert result["phase"] == "awaiting_approval"
    record_path.write_text('[{"record_id": "drifted"}]\n', encoding="utf-8")
    request = _request(config)
    validation = adapter.ProductionGazaAdapter(config, runner=_runner(config, calls=calls), **kwargs).validate(
        request,
        result["phases"]["collection"],
    )
    assert validation.status == "failed"
    assert validation.summary["reason"] == "shared dispatch record history drifted"
    assert calls == [1]
    cached = adapter.run_scheduled_gaza_contract(config, runner=_runner(config, calls=calls), **kwargs)
    assert cached["run_id"] == result["run_id"]
    assert calls == [1]


def test_tampered_record_binding_artifact_fails_closed(tmp_path: Path) -> None:
    source, pages = _make_source(tmp_path)
    record_path = source / RECORD_PATH
    config = _config(tmp_path, source, pages)
    kwargs = {
        "head_reader": lambda _source: HEAD,
        "status_reader": lambda _source: " M data/records/records.json\n",
    }
    result = adapter.run_scheduled_gaza_contract(config, runner=_runner(config), **kwargs)
    collection = result["phases"]["collection"]
    binding_path = Path(collection["summary"]["shared_dispatch_record_binding_path"])
    binding_path.write_bytes(binding_path.read_bytes() + b"tamper")
    validation = adapter.ProductionGazaAdapter(config, runner=_runner(config), **kwargs).validate(
        _request(config),
        collection,
    )
    assert validation.status == "failed"
    assert validation.summary["reason"] == "shared dispatch record binding artifact digest mismatch"
