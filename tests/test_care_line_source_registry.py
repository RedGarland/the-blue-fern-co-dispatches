from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from bluefern_dispatches.care_line_record import JURISDICTIONS_BY_CODE
from bluefern_dispatches.care_line_source_registry import (
    coverage_score,
    coverage_score_components,
    coverage_status,
    load_registry,
    source_readiness_status,
    validate_registry_file,
)
from bluefern_dispatches.care_line_sources import (
    load_pressure_source_registry,
    validate_pressure_source_registry,
)


ROOT = Path(__file__).resolve().parents[1]
DIRECT_REGISTRY_PATH = ROOT / "data" / "dispatches" / "care-line" / "source_registry.json"


def _coverage_rows() -> list[tuple[str, str, int]]:
    registry = load_registry(DIRECT_REGISTRY_PATH, include_disabled=True)
    by_jurisdiction = defaultdict(list)
    for source in registry.sources:
        if source.state:
            by_jurisdiction[source.state].append(source)
    national_support = sum(1 for source in registry.sources if not source.state and source.enabled)
    rows: list[tuple[str, str, int]] = []
    for code in sorted(JURISDICTIONS_BY_CODE):
        components = coverage_score_components(by_jurisdiction.get(code, []), national_source_support=national_support)
        score = coverage_score(components)
        rows.append((code, coverage_status(components, score), score))
    return rows


def test_direct_registry_covers_all_56_supported_jurisdictions() -> None:
    report = validate_registry_file(DIRECT_REGISTRY_PATH)

    assert report["state_count"] == 56
    assert sorted(report["states"]) == sorted(JURISDICTIONS_BY_CODE)
    assert report["enabled_source_count"] > 100
    assert report["disabled_source_count"] >= 40


def test_enabled_sources_have_supported_collection_paths() -> None:
    registry = load_registry(DIRECT_REGISTRY_PATH, include_disabled=True)
    readiness_counts = Counter(source_readiness_status(source) for source in registry.sources)

    assert readiness_counts["BLOCKED"] == 0
    assert readiness_counts["AUTOMATED_READY"] > 0
    assert readiness_counts["AUTOMATED_PARTIAL"] > 0
    assert readiness_counts["DISABLED"] > 0


def test_coverage_scoring_is_deterministic_and_complete() -> None:
    first = _coverage_rows()
    second = _coverage_rows()

    assert first == second
    assert len(first) == 56
    assert {status for _, status, _ in first} <= {"COMPLETE", "STRONG", "PARTIAL", "MINIMAL", "NONE"}
    assert all(score >= 0 for _, _, score in first)


def test_legacy_pressure_registry_still_validates() -> None:
    registry = load_pressure_source_registry(ROOT)

    assert len(registry) == 30
    assert not validate_pressure_source_registry(registry)
