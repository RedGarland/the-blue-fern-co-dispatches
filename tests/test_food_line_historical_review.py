import base64
import hashlib
import json
from pathlib import Path

import pytest

from scripts.import_historical_agent_runs import main


EDITORIAL_RESTRICTIONS = [
    "Use households as the administrative unit.",
    "Distinguish lost benefits from reduced benefits.",
    "Do not invent a countywide count.",
    "Do not infer uniform county impact.",
    "Separate local evidence from statewide policy context.",
    "Do not claim all households experienced skipped meals.",
    "Do not convert provider observations into a measured countywide trend.",
    "Keep April, June, July, October, and FY2027 policy dates distinct.",
    "Recheck currentness before editorial use.",
]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def set_nested(value: dict, path: tuple[str, ...], replacement: object) -> None:
    current = value
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = replacement


def food_line_review_fixture(
    root: Path,
    *,
    location_name: str = "Los Angeles County",
    state: str = "CA",
    run_suffix: str = "los-angeles",
) -> tuple[list[str], dict[str, Path]]:
    raw_bytes = f"private historical Food Line alert: {location_name}".encode("utf-8")
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    finding_id = f"finding-{run_suffix}"
    run_id = f"food-line-source-watch-{run_suffix}"
    source_url = f"https://example.com/food-line/{run_suffix}"
    base = root / "data/agent-history/food-line"
    raw_path = base / "raw" / f"{raw_sha}.json"
    normalized_path = base / "normalized" / f"{raw_sha}.json"
    report_path = base / "reports" / f"{raw_sha}.json"
    review_path = base / "reviews" / f"{raw_sha}-substantive-review.json"

    write_json(
        raw_path,
        {
            "schema_version": "historical_agent_raw_v1",
            "domain": "food-line",
            "raw_sha256": raw_sha,
            "raw_bytes_base64": base64.b64encode(raw_bytes).decode("ascii"),
            "agent_run_id": run_id,
        },
    )
    finding = {
        "agent_finding_id": finding_id,
        "candidate_id": finding_id,
        "agent_duplicate_key": hashlib.sha256(
            f"{source_url}|2026-07-28".encode("utf-8")
        ).hexdigest(),
        "agent_run_id": run_id,
        "canonical_url": source_url,
        "deduplication_outcome": "new_historical_candidate",
        "evidence_level": "news report",
        "evidence_text": "Provider reporting documents increased food-assistance demand.",
        "location_name": location_name,
        "location_scope": "state_local",
        "map_eligible": True,
        "pressure_signal": True,
        "pressure_type": "demand strain",
        "publisher": "Example News",
        "review_status": "pending_review",
        "source_published_date": "2026-07-28",
        "source_role": "local_signal",
        "source_url": source_url,
        "state": state,
        "title": f"Food access pressure in {location_name}",
        "url": source_url,
    }
    write_json(
        normalized_path,
        {
            "schema_version": "historical_agent_normalized_v1",
            "domain": "food-line",
            "raw_sha256": raw_sha,
            "findings": [finding],
        },
    )
    write_json(
        report_path,
        {
            "domain": "food-line",
            "input_sha256": raw_sha,
            "outcomes": {"new_historical_candidate": 1},
            "status": "imported",
        },
    )
    write_json(
        review_path,
        {
            "schema_version": "food_line_substantive_historical_review_v1",
            "domain": "food-line",
            "raw_sha256": raw_sha,
            "normalized_finding_id": finding_id,
            "review_type": "substantive_historical_review",
            "recommended_disposition": "substantively_valid_historical_candidate",
            "archive_mutation_authorized": False,
            "edition_authorized": False,
            "publication_authorized": False,
            "current_review_status": "pending_review",
            "current_publication_eligible": False,
            "current_publication_approval": False,
            "provenance_verification": {
                "agent_run_id": run_id,
                "original_historical_outcome": "new_historical_candidate",
            },
            "source_assessment": {
                "principal_source": {"url": source_url},
            },
            "development_assessment": {
                "geographic_scope": f"{location_name}, {state}",
            },
            "taxonomy_review": {
                "pressure_signal": {"current_value": True},
                "pressure_type": {"current_value": "demand strain"},
                "location_scope": {"current_value": "state_local"},
            },
            "materiality_assessment": {
                "assessment": "moderate_food_access_impact",
            },
            "duplicate_and_public_record_check": {
                "candidate_remains_distinct": True,
                "canonical_source_url_match": None,
                "edition_match": None,
                "historical_duplicate": None,
                "normalized_event_fingerprint_match": None,
                "prior_intake_match": None,
                "public_claim_or_source_ledger_match": None,
                "source_match": None,
            },
            "editorial_restrictions": EDITORIAL_RESTRICTIONS,
        },
    )
    review_sha = hashlib.sha256(review_path.read_bytes()).hexdigest()
    args = [
        "review",
        "--domain",
        "food-line",
        "--raw-sha",
        raw_sha,
        "--decision",
        "substantively-valid",
        "--review-artifact",
        str(review_path),
        "--review-artifact-sha256",
        review_sha,
        "--repo-root",
        str(root),
    ]
    return args, {
        "raw": raw_path,
        "normalized": normalized_path,
        "report": report_path,
        "review": review_path,
    }


def run_json(capsys, args: list[str]) -> tuple[int, dict]:
    code = main(args)
    return code, json.loads(capsys.readouterr().out)


def test_food_line_substantive_review_changes_only_status_and_is_idempotent(
    tmp_path: Path,
    capsys,
):
    protected = [
        tmp_path / "data/dispatches/food-line/agent-inbox/README.md",
        tmp_path / "data/dispatches/food-line/agent-intake/marker.txt",
        tmp_path / "data/dispatches/food-line/editions/marker.txt",
        tmp_path / "output/dispatches/food-line/editions/marker.txt",
        tmp_path / "output/site/food-line/marker.txt",
        tmp_path / "bluefern-dispatches-pages/food-line/marker.txt",
        tmp_path / "data/bluesky/marker.txt",
        tmp_path / "schedules/marker.txt",
        tmp_path / "data/agent-history/care-line/marker.txt",
        tmp_path / "data/agent-history/gaza/marker.txt",
        tmp_path / "data/agent-history/ice/marker.txt",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unchanged", encoding="utf-8")
    protected_bytes = {path: path.read_bytes() for path in protected}

    args, paths = food_line_review_fixture(tmp_path)
    originals = {name: path.read_bytes() for name, path in paths.items()}
    before_normalized = json.loads(originals["normalized"].decode("utf-8"))

    code, accepted = run_json(capsys, args)
    assert code == 0
    assert accepted["status"] == "review_status_updated"
    assert accepted["previous_review_status"] == "pending_review"
    assert accepted["new_review_status"] == "substantively_reviewed"
    assert accepted["inventory_before"]["raw_run_count"] == 1
    assert accepted["inventory_before"]["normalized_finding_count"] == 1
    assert accepted["inventory_before"]["new_historical_candidate_count"] == 1
    assert accepted["inventory_before"]["historical_candidate_count"] == 0
    assert accepted["inventory_before"]["pending_substantive_review"] == 1
    assert accepted["inventory_before"]["substantively_reviewed"] == 0
    assert accepted["inventory_after"]["pending_substantive_review"] == 0
    assert accepted["inventory_after"]["substantively_reviewed"] == 1
    assert accepted["inventory_after"]["publication_ready_count"] == 0

    after_normalized = json.loads(paths["normalized"].read_text(encoding="utf-8"))
    expected = json.loads(json.dumps(before_normalized))
    expected["findings"][0]["review_status"] = "substantively_reviewed"
    assert after_normalized == expected
    assert paths["raw"].read_bytes() == originals["raw"]
    assert paths["report"].read_bytes() == originals["report"]
    assert paths["review"].read_bytes() == originals["review"]
    assert all(path.read_bytes() == protected_bytes[path] for path in protected)

    audit_path = Path(accepted["decision_audit_path"])
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["schema_version"] == "historical_substantive_review_decision_v1"
    assert audit["domain"] == "food-line"
    assert audit["agent_run_id"] == "food-line-source-watch-los-angeles"
    assert audit["decision"] == "accept_substantively_valid_historical_candidate"
    assert audit["operator"] == "William Patton"
    assert audit["previous_review_status"] == "pending_review"
    assert audit["new_review_status"] == "substantively_reviewed"
    assert audit["historical_outcome"] == "new_historical_candidate"
    assert audit["pressure_type"] == "demand strain"
    assert audit["location_scope"] == "state_local"
    assert audit["materiality_assessment"] == "moderate_food_access_impact"
    assert audit["publication_eligible"] is False
    assert audit["publication_approval"] is False
    assert audit["archive_content_change_authorized"] is False
    assert audit["intake_authorized"] is False
    assert audit["edition_authorized"] is False
    assert audit["map_authorized"] is False
    assert audit["publication_authorized"] is False
    assert audit["editorial_restrictions"] == EDITORIAL_RESTRICTIONS
    assert audit["changed_fields"] == ["findings[].review_status"]
    assert len(list(audit_path.parent.glob("*.json"))) == 1

    index_path = tmp_path / "data/agent-history/food-line/reports/history-index.json"
    no_op_hashes = {
        "normalized": hashlib.sha256(paths["normalized"].read_bytes()).hexdigest(),
        "audit": hashlib.sha256(audit_path.read_bytes()).hexdigest(),
        "index": hashlib.sha256(index_path.read_bytes()).hexdigest(),
    }
    decided_at = audit["decided_at"]
    code, repeated = run_json(capsys, args)
    assert code == 0
    assert repeated["status"] == "idempotent_noop"
    assert repeated["inventory"]["pending_substantive_review"] == 0
    assert repeated["inventory"]["substantively_reviewed"] == 1
    assert repeated["inventory"]["publication_ready_count"] == 0
    assert hashlib.sha256(paths["normalized"].read_bytes()).hexdigest() == no_op_hashes["normalized"]
    assert hashlib.sha256(audit_path.read_bytes()).hexdigest() == no_op_hashes["audit"]
    assert hashlib.sha256(index_path.read_bytes()).hexdigest() == no_op_hashes["index"]
    assert json.loads(audit_path.read_text(encoding="utf-8"))["decided_at"] == decided_at
    assert len(list(audit_path.parent.glob("*.json"))) == 1


@pytest.mark.parametrize(
    ("path", "replacement", "message"),
    [
        (("raw_sha256",), "0" * 64, "SHA-256 identity"),
        (("normalized_finding_id",), "wrong-finding", "exactly one normalized finding"),
        (("provenance_verification", "agent_run_id"), "wrong-run", "agent_run_id"),
        (("edition_authorized",), True, "edition_authorized"),
        (("publication_authorized",), True, "publication_authorized"),
        (("materiality_assessment", "assessment"), "context_only", "materiality_assessment"),
    ],
)
def test_food_line_substantive_review_fails_closed_on_review_mismatch(
    tmp_path: Path,
    path: tuple[str, ...],
    replacement: object,
    message: str,
):
    args, paths = food_line_review_fixture(tmp_path)
    review = json.loads(paths["review"].read_text(encoding="utf-8"))
    set_nested(review, path, replacement)
    write_json(paths["review"], review)
    args[args.index("--review-artifact-sha256") + 1] = hashlib.sha256(
        paths["review"].read_bytes()
    ).hexdigest()
    normalized_before = paths["normalized"].read_bytes()

    with pytest.raises(ValueError, match=message):
        main(args)

    assert paths["normalized"].read_bytes() == normalized_before
    assert not (tmp_path / "data/agent-history/food-line/reviews/decisions").exists()


@pytest.mark.parametrize("outcome", ["archived_invalid", "matched_existing"])
def test_food_line_substantive_review_rejects_noncandidate_outcomes(
    tmp_path: Path,
    outcome: str,
):
    args, paths = food_line_review_fixture(tmp_path)
    normalized = json.loads(paths["normalized"].read_text(encoding="utf-8"))
    normalized["findings"][0]["deduplication_outcome"] = outcome
    if outcome == "archived_invalid":
        normalized["findings"][0]["review_status"] = "excluded"
    write_json(paths["normalized"], normalized)
    before = paths["normalized"].read_bytes()

    with pytest.raises(ValueError, match="new_historical_candidate"):
        main(args)

    assert paths["normalized"].read_bytes() == before
    assert not (tmp_path / "data/agent-history/food-line/reviews/decisions").exists()


@pytest.mark.parametrize("field", ["publication_eligible", "publication_approval"])
def test_food_line_substantive_review_rejects_publication_flags(
    tmp_path: Path,
    field: str,
):
    args, paths = food_line_review_fixture(tmp_path)
    normalized = json.loads(paths["normalized"].read_text(encoding="utf-8"))
    normalized["findings"][0][field] = True
    write_json(paths["normalized"], normalized)

    with pytest.raises(ValueError, match=field):
        main(args)

    assert not (tmp_path / "data/agent-history/food-line/reviews/decisions").exists()


@pytest.mark.parametrize("target", ["edition", "intake"])
def test_food_line_substantive_review_rejects_exact_public_or_intake_match(
    tmp_path: Path,
    target: str,
):
    args, paths = food_line_review_fixture(tmp_path)
    source_url = json.loads(paths["normalized"].read_text(encoding="utf-8"))[
        "findings"
    ][0]["source_url"]
    target_path = (
        tmp_path / "output/dispatches/food-line/editions/2026-07-28/sources_manifest.json"
        if target == "edition"
        else tmp_path / "data/dispatches/food-line/agent-intake/2026-07-28/run.json"
    )
    write_json(target_path, {"source_url": source_url})
    before = paths["normalized"].read_bytes()

    with pytest.raises(ValueError, match="exact public, intake"):
        main(args)

    assert paths["normalized"].read_bytes() == before
    assert not (tmp_path / "data/agent-history/food-line/reviews/decisions").exists()


@pytest.mark.parametrize(
    ("location_name", "state", "run_suffix"),
    [
        ("North Texas", "TX", "north-texas"),
        ("Durham County", "NC", "durham"),
    ],
)
def test_food_line_review_is_generic_for_remaining_candidate_shapes(
    tmp_path: Path,
    capsys,
    location_name: str,
    state: str,
    run_suffix: str,
):
    args, paths = food_line_review_fixture(
        tmp_path,
        location_name=location_name,
        state=state,
        run_suffix=run_suffix,
    )
    code, result = run_json(capsys, args)
    assert code == 0
    assert result["new_review_status"] == "substantively_reviewed"
    finding = json.loads(paths["normalized"].read_text(encoding="utf-8"))["findings"][0]
    assert finding["location_name"] == location_name
    assert finding["review_status"] == "substantively_reviewed"
    assert result["inventory_after"]["publication_ready_count"] == 0
