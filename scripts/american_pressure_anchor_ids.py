from __future__ import annotations

from pathlib import Path

from bluefern_dispatches.american_pressure_sources import load_source_registry


def canonical_valid_anchor_ids_by_pillar(root: Path, pillars: tuple[str, ...]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {pillar: [] for pillar in pillars}
    for row in load_source_registry(root):
        source_id = str(row.get("source_id") or "").strip()
        pillar = str(row.get("pillar") or "").strip()
        if not source_id or pillar not in out:
            continue
        source_state = str(row.get("source_state") or ("enabled" if row.get("enabled") else "disabled")).strip()
        if source_state == "disabled":
            continue
        out[pillar].append(source_id)
    return out
