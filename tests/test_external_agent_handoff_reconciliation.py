from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from reconcile_food_care_post_discovery import reconcile

from test_external_agent_handoff import _envelope, _write
from bluefern_dispatches.external_agent_handoff import import_envelope


def test_reconciliation_filters_by_agent_run_and_reports_terminal_disposition(tmp_path: Path) -> None:
    source = tmp_path / "food.json"
    _write(source, _envelope(run_id="reconcile-run"))
    assert import_envelope(tmp_path, source, dispatch="food-line")[0] == 0
    result = reconcile(tmp_path, dispatch="food-line", agent_run_id="reconcile-run", edition_date="2026-09-10")
    assert result["row_count"] == 1
    assert result["unaccounted"] == 0
    assert result["rows"][0]["terminal_disposition"] in {"retained_for_review", "rejected_with_reason", "invalid_source_with_reason"}
