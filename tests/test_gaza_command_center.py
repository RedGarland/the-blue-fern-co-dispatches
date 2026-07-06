from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

import scripts.gaza_command_center as command_center


def _make_root(repo: Path) -> Path:
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "gaza-command-center"
    (root / "bluefern-dispatches-pages").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture()
def isolated(monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = Path(__file__).resolve().parents[1]
    root = _make_root(repo)
    monkeypatch.setattr(command_center, "ROOT", root)
    try:
        yield root
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def _parsed(argv: list[str]) -> object:
    return command_center.parse_args(argv)


def test_date_parsing_accepts_single_date_and_range() -> None:
    single = _parsed(["--date", "2026-07-05"])
    ranged = _parsed(["--start-date", "2026-07-01", "--end-date", "2026-07-05"])

    assert command_center.build_date_list(single) == ["2026-07-05"]
    assert command_center.build_date_list(ranged) == [
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
        "2026-07-04",
        "2026-07-05",
    ]


@pytest.mark.parametrize(
    "argv",
    [
        ["--date", "2026-07-05", "--start-date", "2026-07-01"],
        ["--date", "2026-07-05", "--end-date", "2026-07-05"],
        ["--start-date", "2026-07-05"],
        ["--end-date", "2026-07-05"],
        ["--start-date", "2026-07-05", "--end-date", "2026-07-01"],
    ],
)
def test_date_parsing_rejects_invalid_combinations(argv: list[str]) -> None:
    args = _parsed(argv)
    with pytest.raises(ValueError):
        command_center.build_date_list(args)


def test_production_mode_without_actions_does_not_trigger_public_side_effects(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_center, "_preflight_report", lambda: {"ok": True, "source_repo": {"summary": {"entry_count": 0, "risky_entries": [], "allowed_entries": []}}, "pages_repo": {"summary": {"entry_count": 0, "risky_entries": [], "allowed_entries": []}}})
    monkeypatch.setattr(command_center, "_manual_source_status", lambda date_text: {"status": "valid", "record_count": 2, "errors": []})

    called = {"email": 0, "publish": 0, "bluesky": 0}
    monkeypatch.setattr(command_center, "_run_publish", lambda *args, **kwargs: called.__setitem__("publish", called["publish"] + 1) or pytest.fail("publish should not run"))
    monkeypatch.setattr(command_center, "_run_post_bluesky", lambda *args, **kwargs: called.__setitem__("bluesky", called["bluesky"] + 1) or pytest.fail("bluesky should not run"))
    monkeypatch.setattr(command_center, "_run_email", lambda *args, **kwargs: called.__setitem__("email", called["email"] + 1) or pytest.fail("email should not run"))

    report = command_center.build_report(_parsed(["--date", "2026-07-05", "--production"]))

    assert report["ok"] is True
    assert report["dates"][0]["actions"] == []
    assert called == {"email": 0, "publish": 0, "bluesky": 0}


def test_test_mode_plans_publish_post_and_email_without_public_side_effects(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_TO", "ops@example.com")
    monkeypatch.setattr(command_center, "_preflight_report", lambda: {"ok": True, "source_repo": {"summary": {"entry_count": 0, "risky_entries": [], "allowed_entries": []}}, "pages_repo": {"summary": {"entry_count": 0, "risky_entries": [], "allowed_entries": []}}})
    monkeypatch.setattr(command_center, "_manual_source_status", lambda date_text: {"status": "valid", "record_count": 2, "errors": []})
    monkeypatch.setattr(command_center.daily_operator, "run_operator", lambda *args, **kwargs: pytest.fail("publish must be planned in test mode"))
    monkeypatch.setattr(command_center, "send_email", lambda *args, **kwargs: pytest.fail("email must not be sent in test mode"))

    bluesky_calls: list[dict[str, object]] = []

    def fake_bluesky(**kwargs: object) -> dict[str, object]:
        bluesky_calls.append(kwargs)
        return {"status": "planned", "card_title": "Preview card", "post_text": "Preview", "source_artifact_paths": []}

    monkeypatch.setattr(command_center, "maybe_post_gaza_dispatch_to_bluesky", fake_bluesky)

    report = command_center.build_report(_parsed(["--date", "2026-07-05", "--publish", "--post-bluesky", "--email"]))
    actions = report["dates"][0]["actions"]

    assert [action["action"] for action in actions] == ["publish", "post_bluesky", "email"]
    assert all(action["execution"] == "plan" for action in actions)
    assert actions[0]["status"] == "planned"
    assert actions[1]["status"] == "planned"
    assert actions[2]["status"] == "planned"
    assert bluesky_calls and bluesky_calls[0]["allow_publish"] is False


def test_range_processing_is_ascending(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_run_date(date_text: str, args: object, selected_actions: list[str]) -> dict[str, object]:
        seen.append(date_text)
        return {"date": date_text, "mode": "test", "selected_actions": selected_actions, "repos": {"source": {}, "pages": {}}, "manual_sources": {"status": "valid", "record_count": 0, "errors": []}, "preflight": {"ok": True}, "actions": [], "ok": True, "stopped_early": False, "next_safe_action": "No action needed."}

    monkeypatch.setattr(command_center, "_run_date", fake_run_date)

    report = command_center.build_report(_parsed(["--start-date", "2026-07-01", "--end-date", "2026-07-05"]))

    assert seen == ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"]
    assert report["processed_dates"] == seen


def test_production_range_stops_on_first_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_run_date(date_text: str, args: object, selected_actions: list[str]) -> dict[str, object]:
        seen.append(date_text)
        ok = date_text != "2026-07-02"
        return {"date": date_text, "mode": "production", "selected_actions": selected_actions, "repos": {"source": {}, "pages": {}}, "manual_sources": {"status": "valid", "record_count": 0, "errors": []}, "preflight": {"ok": True}, "actions": [], "ok": ok, "stopped_early": not ok, "next_safe_action": "No action needed." if ok else "Fix the failed publish action and rerun."}

    monkeypatch.setattr(command_center, "_run_date", fake_run_date)

    report = command_center.build_report(_parsed(["--start-date", "2026-07-01", "--end-date", "2026-07-03", "--production", "--publish"]))

    assert seen == ["2026-07-01", "2026-07-02"]
    assert report["failed_dates"] == ["2026-07-02"]
    assert report["ok"] is False


def test_dry_run_full_invokes_wrapper_with_expected_flags(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_center, "_preflight_report", lambda: {"ok": True, "source_repo": {"summary": {"entry_count": 0, "risky_entries": [], "allowed_entries": []}}, "pages_repo": {"summary": {"entry_count": 0, "risky_entries": [], "allowed_entries": []}}})
    monkeypatch.setattr(command_center, "_manual_source_status", lambda date_text: {"status": "valid", "record_count": 2, "errors": []})

    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return type("Completed", (), {"returncode": 0, "stdout": "Isolated Gaza dry-run workspace: C:\\tmp\\gaza\n", "stderr": ""})()

    monkeypatch.setattr(command_center.subprocess, "run", fake_run)

    report = command_center.build_report(_parsed(["--date", "2026-07-05", "--dry-run-full"]))
    action = report["dates"][0]["actions"][0]

    assert action["status"] == "passed"
    assert action["details"]["workspace"] == "C:\\tmp\\gaza"
    assert "-DryRunFull" in captured["cmd"]
    assert "-Push" not in captured["cmd"]
    assert "-PostBluesky" not in captured["cmd"]


def test_json_output_includes_dates_and_aggregate(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        command_center,
        "_run_date",
        lambda date_text, args, selected_actions: {
            "date": date_text,
            "mode": "test",
            "selected_actions": selected_actions,
            "repos": {"source": {"state": "clean"}, "pages": {"state": "clean"}},
            "manual_sources": {"status": "valid", "record_count": 2, "errors": []},
            "preflight": {"ok": True},
            "actions": [],
            "ok": True,
            "stopped_early": False,
            "next_safe_action": "No action needed.",
        },
    )

    code = command_center.main(["--date", "2026-07-05", "--check", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["dates"][0]["date"] == "2026-07-05"
    assert payload["aggregate"]["date_count"] == 1
    assert payload["ok"] is True
