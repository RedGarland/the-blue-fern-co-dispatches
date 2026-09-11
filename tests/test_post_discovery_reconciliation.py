from __future__ import annotations

import json

from scripts.reconcile_food_care_post_discovery import main


def test_bounded_reconciliation_accounts_for_food_terminal_rows(tmp_path, capsys):
    intake = tmp_path / "data/dispatches/food-line/agent-intake/2026-09-10"
    intake.mkdir(parents=True)
    (intake / "run.json").write_text(
        json.dumps(
            {
                "agent_run_id": "run-1",
                "candidate_rows": [
                    {
                        "title": "Pantry closes",
                        "source_url": "https://example.org/pantry",
                        "candidate_disposition": "retained_for_review",
                    },
                    {
                        "title": "Duplicate pantry closes",
                        "source_url": "https://example.org/pantry",
                        "candidate_disposition": "duplicate_with_reason",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    assert main(
        [
            "--root", str(tmp_path),
            "--dispatch", "food-line",
            "--run-id", "run-1",
            "--date-from", "2026-09-10",
            "--date-to", "2026-09-10",
        ]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["source_fetch_performed"] is False
    assert report["publication_performed"] is False
    assert report["unaccounted_count"] == 0
    assert len(report["rows"]) == 2
