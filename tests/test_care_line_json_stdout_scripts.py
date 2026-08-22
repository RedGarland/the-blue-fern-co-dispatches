from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _BinaryStdout:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()


def test_run_care_line_national_pipeline_emits_utf8_json(monkeypatch) -> None:
    repo = Path(__file__).resolve().parents[1]
    module = _load_module(repo / "scripts" / "run_care_line_national_pipeline.py", "care_line_national_pipeline_cli_test")
    fake_stdout = _BinaryStdout()
    monkeypatch.setattr(module.sys, "stdout", fake_stdout, raising=False)
    monkeypatch.setattr(
        module,
        "run_national_pipeline",
        lambda *args, **kwargs: {"run_manifest": {"status": "success"}, "message": "Care Line"},
        raising=False,
    )

    exit_code = module.main(["--repo-root", str(repo), "--run-date", "2026-08-21", "--collection-only"])

    assert exit_code == 0
    output = fake_stdout.buffer.getvalue().decode("utf-8")
    assert "Care Line" in output
    assert '"status": "success"' in output


def test_run_care_line_reviewed_event_queue_emits_utf8_json(monkeypatch) -> None:
    repo = Path(__file__).resolve().parents[1]
    module = _load_module(repo / "scripts" / "run_care_line_reviewed_event_queue.py", "care_line_reviewed_event_queue_cli_test")
    fake_stdout = _BinaryStdout()
    monkeypatch.setattr(module.sys, "stdout", fake_stdout, raising=False)
    monkeypatch.setattr(
        module,
        "run_queue_poll",
        lambda root, max_events=5: {"ok": True, "status": "ready", "message": "Care Line"},
        raising=False,
    )

    exit_code = module.main(["--repo-root", str(repo), "--max-events", "2"])

    assert exit_code == 0
    output = fake_stdout.buffer.getvalue().decode("utf-8")
    assert "Care Line" in output
    assert '"status": "ready"' in output
