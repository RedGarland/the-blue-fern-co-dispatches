from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import scripts.run_food_line_discovery_expansion as expansion_module


def _result() -> dict[str, object]:
    return {
        "task_started_at": "2026-10-17T23:40:23Z",
        "task_completed_at": "2026-10-17T23:45:42Z",
        "candidates": [{"candidate_id": "synthetic-1", "title": "Synthetic food signal"}],
    }


def test_agent_export_hash_matches_written_bytes(tmp_path: Path) -> None:
    export = expansion_module._export_agent_inbox(
        tmp_path,
        edition_date="2026-10-17",
        run_id="synthetic-run-1",
        result=_result(),
        agent_inbox_dir=str(tmp_path / "agent-inbox"),
    )

    export_path = Path(str(export["path"]))
    assert export["sha256"] == hashlib.sha256(export_path.read_bytes()).hexdigest()


def test_agent_export_hash_matches_windows_crlf_bytes(tmp_path: Path, monkeypatch) -> None:
    def write_crlf(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = (json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n").replace(
            "\n", "\r\n"
        )
        path.write_bytes(serialized.encode("utf-8"))

    monkeypatch.setattr(expansion_module, "_atomic_write_json", write_crlf)
    export = expansion_module._export_agent_inbox(
        tmp_path,
        edition_date="2026-10-17",
        run_id="synthetic-run-crlf",
        result=_result(),
        agent_inbox_dir=str(tmp_path / "agent-inbox"),
    )

    export_path = Path(str(export["path"]))
    assert export["sha256"] == hashlib.sha256(export_path.read_bytes()).hexdigest()


def test_agent_export_does_not_read_after_write_failure(tmp_path: Path, monkeypatch) -> None:
    def fail_write(path: Path, payload: object) -> None:
        raise OSError("simulated write failure")

    def unexpected_read(path: Path) -> bytes:
        raise AssertionError("read_bytes called after write failure")

    monkeypatch.setattr(expansion_module, "_atomic_write_json", fail_write)
    monkeypatch.setattr(Path, "read_bytes", unexpected_read)

    with pytest.raises(OSError, match="simulated write failure"):
        expansion_module._export_agent_inbox(
            tmp_path,
            edition_date="2026-10-17",
            run_id="synthetic-run-write-failure",
            result=_result(),
            agent_inbox_dir=str(tmp_path / "agent-inbox"),
        )
