"""Private, content-addressed recovery for aggregate Food Line agent handoffs."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent_findings import finding_from_payload, normalize_source_url


RAW_SCHEMA = "food_line_historical_recovery_raw_v1"
SPEC_SCHEMA = "food_line_historical_event_cluster_spec_v1"
RECOVERY_SCHEMA = "food_line_historical_recovery_v1"
MIGRATION_SCHEMA = "food_line_historical_recovery_migration_v1"
FIVE_TIER_PRIORITY_POLICY = "food_line_historical_priority_policy_v1_five_tier"
FOUR_TIER_PRIORITY_POLICY = "food_line_historical_priority_policy_v2_four_tier"
FOUR_TIER_PRIORITY_SEMANTICS = {
    "tier_1": "closures_suspensions_and_direct_service_reductions",
    "tier_2": "measured_benefit_loss_with_emergency_food_demand",
    "tier_3": "quantified_inventory_supply_or_capacity_strain",
    "tier_4": "other_demonstrated_access_affordability_or_disaster_related_food_losses",
}
RECOVERY_ARTIFACT_SCHEMAS = {
    "raw_archive.json": RAW_SCHEMA,
    "normalized_unique_sources.json": "food_line_historical_unique_sources_v1",
    "normalized_findings.json": "food_line_historical_normalized_findings_v1",
    "event_cluster_manifest.json": "food_line_historical_event_clusters_v1",
    "live_site_reconciliation_report.json": "food_line_historical_reconciliation_v1",
    "disposition_matrix.json": "food_line_historical_dispositions_v1",
    "import_validation_report.json": "food_line_historical_recovery_validation_v1",
    "priority_confirmed_candidates.json": "food_line_historical_priority_candidates_v1",
}
DISPOSITIONS = {
    "already_published",
    "duplicate_or_corroboration",
    "confirmed_historical_review_candidate",
    "deferred_specific_evidence_gap",
    "excluded_under_existing_rules",
}
HISTORICAL_EVENT_REVIEW_SCHEMA = "food_line_historical_event_editorial_review_v1"
HISTORICAL_EVENT_DECISION_SCHEMA = "food_line_historical_event_editorial_decision_v1"
HISTORICAL_EVENT_DECISIONS = {
    "confirmed",
    "deferred_specific_evidence_gap",
    "excluded_under_existing_rules",
    "duplicate_or_corroboration",
    "already_published",
}
HISTORICAL_DEDUPE_SURFACES = {
    "food_line_pages_history",
    "source_and_generated_output",
    "story_memory_and_claim_ledgers",
    "current_intake",
    "review_and_publication_queues",
    "existing_historical_records",
}
UNCERTAINTY_STATUSES = {"resolved", "unresolved", "not_applicable"}
CONSEQUENCE_PRIORITIES = {
    "direct_service_loss_or_closure": 1,
    "benefit_access_contraction_with_emergency_demand": 2,
    "inventory_or_capacity_strain": 3,
    "grocery_or_school_meal_access_loss": 4,
    "disaster_household_food_loss": 4,
    "risk_or_mitigation_only": 4,
}
PUBLIC_CATEGORIES = {"source_site_output", "pages_public_output"}


class FoodLineHistoricalRecoveryError(ValueError):
    """Raised when a private recovery input or cluster specification fails closed."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": ")) + "\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fingerprint(value: Any) -> str:
    return "sha256:" + sha256_bytes(canonical_json(value).encode("utf-8"))


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _identity_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _text(value).lower()).strip()


def _date(value: Any, field: str, *, required: bool = True) -> str:
    text = str(value or "").strip()[:10]
    if not text and not required:
        return ""
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise FoodLineHistoricalRecoveryError(f"{field} must be YYYY-MM-DD") from exc
    return text


def _timestamp(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise FoodLineHistoricalRecoveryError(f"{field} is required")
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FoodLineHistoricalRecoveryError(f"{field} must be an ISO-8601 timestamp") from exc
    return text


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the sibling temporary name short enough for Windows worktrees while
    # retaining atomic replacement on the destination volume.
    descriptor, temporary = tempfile.mkstemp(prefix=".write.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(value))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parse_aggregate_handoff(raw: bytes, *, run_month: str | None = None) -> dict[str, Any]:
    """Parse every valid JSON fence while retaining whole-file provenance."""
    if run_month is not None and not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", run_month):
        raise FoodLineHistoricalRecoveryError("run_month must be YYYY-MM")
    input_sha256 = sha256_bytes(raw)
    text = raw.decode("utf-8", errors="replace")
    pattern = re.compile(r"(?ms)^```([^\r\n`]*)\r?\n(.*?)^```[ \t]*(?:\r?\n|$)")
    fences = list(pattern.finditer(text))
    malformed: list[dict[str, Any]] = []
    invalid_envelopes: list[dict[str, Any]] = []
    invalid_findings: list[dict[str, Any]] = []
    out_of_scope_findings: list[dict[str, Any]] = []
    occurrences: list[dict[str, Any]] = []

    for fence_index, fence in enumerate(fences, start=1):
        label = fence.group(1).strip().lower()
        line = text.count("\n", 0, fence.start()) + 1
        block = fence.group(2)
        block_sha = sha256_bytes(block.encode("utf-8"))
        if label not in {"", "json"}:
            malformed.append(
                {
                    "fence_index": fence_index,
                    "line": line,
                    "label": label,
                    "block_sha256": block_sha,
                    "reason": "unsupported_fence_label",
                }
            )
            continue
        try:
            envelope = json.loads(block)
        except json.JSONDecodeError as exc:
            malformed.append(
                {
                    "fence_index": fence_index,
                    "line": line,
                    "label": label or "<unlabeled>",
                    "block_sha256": block_sha,
                    "reason": "invalid_json",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        if not isinstance(envelope, dict) or not isinstance(envelope.get("findings"), list):
            invalid_envelopes.append(
                {
                    "fence_index": fence_index,
                    "line": line,
                    "block_sha256": block_sha,
                    "reason": "json_fence_is_not_agent_run_envelope",
                }
            )
            continue
        run_id = _text(envelope.get("agent_run_id"))
        agent_name = _text(envelope.get("agent_name"))
        discovered_at = _text(envelope.get("completed_at") or envelope.get("started_at"))
        for finding_index, payload in enumerate(envelope["findings"], start=1):
            occurrence_id = f"occurrence-{input_sha256[:12]}-{fence_index:04d}-{finding_index:04d}"
            if not isinstance(payload, dict):
                invalid_findings.append(
                    {
                        "occurrence_id": occurrence_id,
                        "fence_index": fence_index,
                        "finding_index": finding_index,
                        "reason": "finding_is_not_object",
                    }
                )
                continue
            try:
                finding = finding_from_payload(
                    payload,
                    agent_name=agent_name,
                    agent_run_id=run_id,
                    discovered_at=discovered_at,
                )
            except (TypeError, ValueError) as exc:
                invalid_findings.append(
                    {
                        "occurrence_id": occurrence_id,
                        "fence_index": fence_index,
                        "finding_index": finding_index,
                        "reason": "finding_normalization_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            if run_month is not None and not discovered_at.startswith(run_month + "-"):
                out_of_scope_findings.append(
                    {
                        "occurrence_id": occurrence_id,
                        "fence_index": fence_index,
                        "finding_index": finding_index,
                        "agent_run_id": run_id,
                        "completed_or_started_at": discovered_at,
                        "canonical_source_url": finding.canonical_source_url,
                        "title": finding.title,
                        "reason": "agent_run_outside_requested_month",
                    }
                )
                continue
            normalized_payload = {
                "agent_run_id": run_id,
                "agent_name": agent_name,
                "started_at": _text(envelope.get("started_at")),
                "completed_at": _text(envelope.get("completed_at")),
                "search_window": envelope.get("search_window") or {},
                "source_url": finding.source_url,
                "canonical_source_url": finding.canonical_source_url,
                "publisher": finding.publisher,
                "source_published_at": finding.source_published_at,
                "title": finding.title,
                "exact_supporting_passage": finding.exact_supporting_passage,
                "summary": finding.summary,
                "location_name": finding.location_name,
                "state": finding.state,
                "location_scope": finding.location_scope,
                "affected_groups": finding.affected_groups,
                "pressure_type": finding.pressure_type,
                "confidence": finding.confidence,
                "source_role": finding.source_role,
                "evidence_level": finding.evidence_level,
                "agent_query_context": finding.agent_query_context,
                "uncertainty": (
                    payload.get("uncertainty")
                    or (payload.get("raw_agent_payload") or {}).get("uncertainty")
                    if isinstance(payload.get("raw_agent_payload"), dict)
                    else payload.get("uncertainty")
                ),
                "raw_finding": payload,
            }
            record_fingerprint = _fingerprint(normalized_payload)
            occurrences.append(
                {
                    "occurrence_id": occurrence_id,
                    "fence_index": fence_index,
                    "finding_index": finding_index,
                    "record_fingerprint": record_fingerprint,
                    **normalized_payload,
                }
            )

    by_fingerprint: dict[str, dict[str, Any]] = {}
    for occurrence in occurrences:
        fingerprint = occurrence["record_fingerprint"]
        if fingerprint not in by_fingerprint:
            finding_id = "food-line-history-finding-" + fingerprint.removeprefix("sha256:")[:24]
            retained = dict(occurrence)
            retained["finding_id"] = finding_id
            retained["occurrence_ids"] = [occurrence["occurrence_id"]]
            retained.pop("occurrence_id", None)
            by_fingerprint[fingerprint] = retained
        else:
            by_fingerprint[fingerprint]["occurrence_ids"].append(occurrence["occurrence_id"])
    findings = sorted(by_fingerprint.values(), key=lambda item: item["finding_id"])
    for finding in findings:
        finding["duplicate_occurrence_count"] = len(finding["occurrence_ids"]) - 1

    source_map: dict[str, dict[str, Any]] = {}
    for finding in findings:
        canonical = finding["canonical_source_url"]
        if canonical:
            source_key = canonical
            source_id = "food-line-history-source-" + sha256_bytes(canonical.encode("utf-8"))[:24]
        else:
            source_key = "invalid:" + finding["finding_id"]
            source_id = "food-line-history-source-invalid-" + finding["finding_id"].rsplit("-", 1)[-1]
        source = source_map.setdefault(
            source_key,
            {
                "source_id": source_id,
                "canonical_source_url": canonical,
                "original_source_urls": [],
                "publishers": [],
                "source_publication_dates": [],
                "finding_ids": [],
            },
        )
        source["original_source_urls"].append(finding["source_url"])
        source["publishers"].append(finding["publisher"])
        source["source_publication_dates"].append(finding["source_published_at"])
        source["finding_ids"].append(finding["finding_id"])
    sources = []
    for source in source_map.values():
        for key in ("original_source_urls", "publishers", "source_publication_dates", "finding_ids"):
            source[key] = sorted({value for value in source[key] if value})
        sources.append(source)
    sources.sort(key=lambda item: item["source_id"])

    return {
        "input_sha256": input_sha256,
        "run_month": run_month,
        "fence_count": len(fences),
        "valid_json_block_count": len(fences) - len(malformed) - len(invalid_envelopes),
        "malformed_json_block_count": len(malformed),
        "invalid_envelope_block_count": len(invalid_envelopes),
        "raw_finding_count": len(occurrences) + len(invalid_findings) + len(out_of_scope_findings),
        "out_of_scope_finding_count": len(out_of_scope_findings),
        "retained_finding_count": len(findings),
        "duplicate_finding_occurrence_count": len(occurrences) - len(findings),
        "unique_canonical_source_count": sum(1 for item in sources if item["canonical_source_url"]),
        "malformed_blocks": malformed,
        "invalid_envelopes": invalid_envelopes,
        "invalid_findings": invalid_findings,
        "out_of_scope_findings": out_of_scope_findings,
        "findings": findings,
        "sources": sources,
    }


def cluster_spec_template(parsed: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic unassigned template; it makes no editorial grouping decision."""
    return {
        "schema_version": SPEC_SCHEMA,
        "input_sha256": parsed["input_sha256"],
        "run_month": parsed["run_month"],
        "reviewed_by": "",
        "reviewed_at": "",
        "publication_approval": False,
        "unassigned_finding_ids": sorted(item["finding_id"] for item in parsed["findings"]),
        "clusters": [],
    }


@dataclass(frozen=True)
class _CurrentRecoveryIdentity:
    input_sha256: str
    repository_root: Path
    recovery_root: Path
    target: Path


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError as exc:
        raise FoodLineHistoricalRecoveryError(f"unable to inspect recovery path: {path}") from exc
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _validated_current_recovery_identity(root: Path, input_sha256: str) -> _CurrentRecoveryIdentity:
    if not re.fullmatch(r"[0-9a-f]{64}", input_sha256):
        raise FoodLineHistoricalRecoveryError("recovery input_sha256 must be exactly 64 lowercase hexadecimal characters")
    absolute_root = root.absolute()
    if not absolute_root.exists() or _is_reparse_point(absolute_root):
        raise FoodLineHistoricalRecoveryError("recovery repository root must be an existing real directory")
    try:
        repository_root = absolute_root.resolve(strict=True)
    except OSError as exc:
        raise FoodLineHistoricalRecoveryError("recovery repository root does not resolve") from exc
    if not repository_root.is_dir() or _is_reparse_point(repository_root):
        raise FoodLineHistoricalRecoveryError("recovery repository root must be a real directory")

    recovery_root = repository_root / "data" / "agent-history" / "food-line" / "recoveries"
    target = recovery_root / f"sha256-{input_sha256[:32]}"
    try:
        target.relative_to(recovery_root)
    except ValueError as exc:  # pragma: no cover - constructed components are fixed
        raise FoodLineHistoricalRecoveryError("current recovery target escaped the private recovery root") from exc
    if target.parent != recovery_root:
        raise FoodLineHistoricalRecoveryError("current recovery target must be a direct child of the private recovery root")
    if recovery_root.is_dir():
        for candidate in recovery_root.iterdir():
            if os.path.normcase(candidate.name) == os.path.normcase(target.name) and candidate.name != target.name:
                raise FoodLineHistoricalRecoveryError("current recovery target uses a non-canonical case alias")

    current = repository_root
    for component in ("data", "agent-history", "food-line", "recoveries", target.name):
        current = current / component
        if current.exists() and _is_reparse_point(current):
            raise FoodLineHistoricalRecoveryError(f"current recovery path contains a symlink or junction: {current}")

    if target.exists():
        if not target.is_dir():
            raise FoodLineHistoricalRecoveryError("current recovery target exists but is not a directory")
        try:
            resolved_target = target.resolve(strict=True)
        except OSError as exc:
            raise FoodLineHistoricalRecoveryError("current recovery target does not resolve") from exc
        if not _same_path(resolved_target, target):
            raise FoodLineHistoricalRecoveryError("current recovery target resolves through an alias")
        manifest_path = target / "recovery_manifest.json"
        if not manifest_path.is_file() or _is_reparse_point(manifest_path):
            raise FoodLineHistoricalRecoveryError("current recovery manifest is missing or unsafe")
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FoodLineHistoricalRecoveryError("current recovery manifest is missing or invalid") from exc
        if (
            existing_manifest.get("schema_version") != RECOVERY_SCHEMA
            or existing_manifest.get("input_sha256") != input_sha256
        ):
            raise FoodLineHistoricalRecoveryError("current recovery manifest does not bind the validated recovery identity")

    return _CurrentRecoveryIdentity(
        input_sha256=input_sha256,
        repository_root=repository_root,
        recovery_root=recovery_root,
        target=target,
    )


def _collect_urls(base: Path, *, current_recovery: _CurrentRecoveryIdentity | None = None) -> dict[str, list[str]]:
    matches: dict[str, list[str]] = {}
    if not base.exists():
        return matches
    if base.is_file():
        paths = [base]
    else:
        paths = []
        for directory, child_directories, filenames in os.walk(base, followlinks=False):
            directory_path = Path(directory)
            child_directories.sort()
            filenames.sort()
            if current_recovery is not None:
                retained_directories = []
                for name in child_directories:
                    child = directory_path / name
                    if _same_path(child, current_recovery.target):
                        continue
                    if _is_reparse_point(child):
                        raise FoodLineHistoricalRecoveryError(
                            f"historical recovery scan encountered a symlink or junction: {child}"
                        )
                    retained_directories.append(name)
                child_directories[:] = retained_directories
                for name in filenames:
                    child = directory_path / name
                    if _is_reparse_point(child):
                        raise FoodLineHistoricalRecoveryError(
                            f"historical recovery scan encountered a symlink or junction: {child}"
                        )
            paths.extend(directory_path / name for name in filenames)
    url_pattern = re.compile(r"https://[^\s\"'<>]+", flags=re.I)
    for path in sorted(paths):
        if path.suffix.lower() not in {".csv", ".html", ".json", ".jsonl", ".md", ".txt", ".xml"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for raw_url in url_pattern.findall(text):
            canonical = normalize_source_url(raw_url.rstrip(".,;:)]}"))
            if canonical:
                matches.setdefault(canonical, []).append(str(path))
    return {url: sorted(set(paths)) for url, paths in matches.items()}


def _build_reconciliation(
    root: Path,
    pages_root: Path | None,
    sources: list[dict[str, Any]],
    *,
    current_recovery: _CurrentRecoveryIdentity | None,
) -> dict[str, Any]:
    categories = {
        "source_site_output": [root / "output" / "site" / "food-line"],
        "source_generated_output": [root / "output" / "dispatches" / "food-line" / "editions"],
        "pages_public_output": [pages_root / "food-line"] if pages_root else [],
        "source_records_and_claim_ledgers": [
            root / "data" / "dispatches" / "food-line" / "sources",
            root / "data" / "dispatches" / "food-line" / "normalized",
            root / "data" / "dispatches" / "food-line" / "curated",
            root / "data" / "dispatches" / "food-line" / "editions",
            root / "data" / "records",
        ],
        "agent_intake_and_inbox": [
            root / "data" / "dispatches" / "food-line" / "agent-intake",
            root / "data" / "dispatches" / "food-line" / "agent-inbox",
        ],
        "review_and_publication_queues": [
            root / "data" / "dispatches" / "food-line" / "review",
            root / "data" / "universal_events" / "publication-state",
        ],
        "historical_agent_records": [root / "data" / "agent-history" / "food-line"],
    }
    indexes: dict[str, dict[str, list[str]]] = {}
    for category, paths in categories.items():
        merged: dict[str, list[str]] = {}
        for path in paths:
            exclusion = current_recovery if category == "historical_agent_records" else None
            for url, matched_paths in _collect_urls(path, current_recovery=exclusion).items():
                merged.setdefault(url, []).extend(matched_paths)
        indexes[category] = {url: sorted(set(values)) for url, values in merged.items()}
    source_rows = []
    for source in sources:
        url = source["canonical_source_url"]
        matches = {
            category: index[url]
            for category, index in indexes.items()
            if url and url in index
        }
        source_rows.append(
            {
                "source_id": source["source_id"],
                "canonical_source_url": url,
                "matches": matches,
                "published_exact_url_match": bool(PUBLIC_CATEGORIES.intersection(matches)),
            }
        )
    return {
        "schema_version": "food_line_historical_reconciliation_v1",
        "source_count": len(source_rows),
        "exact_url_match_counts": {
            category: sum(1 for row in source_rows if category in row["matches"])
            for category in categories
        },
        "sources": source_rows,
    }


def build_reconciliation(root: Path, pages_root: Path | None, sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Reconcile normally without exposing a caller-controlled path exclusion."""
    return _build_reconciliation(root, pages_root, sources, current_recovery=None)


def _reference_exists(root: Path, pages_root: Path | None, reference: str) -> bool:
    candidate = Path(reference)
    if candidate.is_absolute():
        return False
    for base in (root, pages_root):
        if base is None:
            continue
        resolved_base = base.resolve()
        resolved = (base / candidate).resolve()
        try:
            resolved.relative_to(resolved_base)
        except ValueError:
            continue
        if resolved.is_file():
            return True
    return False


def validate_clusters(
    spec: dict[str, Any],
    parsed: dict[str, Any],
    reconciliation: dict[str, Any],
    *,
    root: Path,
    pages_root: Path | None,
) -> list[dict[str, Any]]:
    if spec.get("schema_version") != SPEC_SCHEMA:
        raise FoodLineHistoricalRecoveryError(f"cluster spec schema_version must be {SPEC_SCHEMA}")
    if spec.get("input_sha256") != parsed["input_sha256"]:
        raise FoodLineHistoricalRecoveryError("cluster spec input_sha256 does not match the raw handoff")
    if spec.get("run_month") != parsed["run_month"]:
        raise FoodLineHistoricalRecoveryError("cluster spec run_month does not match the requested recovery scope")
    if not _text(spec.get("reviewed_by")):
        raise FoodLineHistoricalRecoveryError("cluster spec requires reviewed_by")
    _timestamp(spec.get("reviewed_at"), "cluster spec reviewed_at")
    if spec.get("publication_approval") is not False:
        raise FoodLineHistoricalRecoveryError("cluster spec publication_approval must be false")
    if spec.get("unassigned_finding_ids") not in (None, []):
        raise FoodLineHistoricalRecoveryError("cluster spec retains unassigned finding IDs")
    clusters = spec.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        raise FoodLineHistoricalRecoveryError("cluster spec clusters must be a non-empty list")

    findings = {item["finding_id"]: item for item in parsed["findings"]}
    reconciliation_by_url = {
        item["canonical_source_url"]: item for item in reconciliation["sources"] if item["canonical_source_url"]
    }
    assigned: list[str] = []
    fingerprints: set[str] = set()
    normalized_clusters: list[dict[str, Any]] = []
    for index, submitted in enumerate(clusters, start=1):
        if not isinstance(submitted, dict):
            raise FoodLineHistoricalRecoveryError(f"cluster {index} must be an object")
        finding_ids = sorted(set(str(value) for value in submitted.get("finding_ids") or []))
        if not finding_ids or any(value not in findings for value in finding_ids):
            raise FoodLineHistoricalRecoveryError(f"cluster {index} contains missing or unknown finding IDs")
        primary = str(submitted.get("primary_finding_id") or "")
        if primary not in finding_ids:
            raise FoodLineHistoricalRecoveryError(f"cluster {index} primary_finding_id must belong to the cluster")
        event_start = _date(submitted.get("event_start_date"), f"cluster {index} event_start_date")
        event_end = _date(submitted.get("event_end_date") or event_start, f"cluster {index} event_end_date")
        if event_end < event_start:
            raise FoodLineHistoricalRecoveryError(f"cluster {index} event period is reversed")
        identity = {
            "location": _identity_text(submitted.get("location")),
            "organization": _identity_text(submitted.get("organization")),
            "event_start_date": event_start,
            "event_end_date": event_end,
            "pressure_category": _identity_text(submitted.get("pressure_category")),
            "underlying_development": _identity_text(submitted.get("underlying_development")),
        }
        if any(not value for value in identity.values()):
            raise FoodLineHistoricalRecoveryError(f"cluster {index} event identity fields are incomplete")
        event_fingerprint = _fingerprint({"domain": "food-line", **identity})
        if event_fingerprint in fingerprints:
            raise FoodLineHistoricalRecoveryError("two clusters have the same stable event identity and must be consolidated")
        fingerprints.add(event_fingerprint)
        event_id = "food-line-event-" + event_fingerprint.removeprefix("sha256:")[:24]
        disposition = str(submitted.get("proposed_disposition") or "")
        if disposition not in DISPOSITIONS:
            raise FoodLineHistoricalRecoveryError(f"cluster {index} proposed_disposition is unsupported")
        disposition_reason = _text(submitted.get("disposition_reason"))
        if not disposition_reason:
            raise FoodLineHistoricalRecoveryError(f"cluster {index} requires a specific disposition_reason")
        if _identity_text(disposition_reason) in {
            "insufficient evidence",
            "not enough evidence",
            "needs review",
            "unknown",
        }:
            raise FoodLineHistoricalRecoveryError(
                f"cluster {index} disposition_reason must identify the event-specific evidence or rule"
            )
        uncertainty = submitted.get("uncertainty")
        if not isinstance(uncertainty, dict):
            raise FoodLineHistoricalRecoveryError(f"cluster {index} uncertainty must be an object")
        normalized_uncertainty = {}
        for kind in ("condition", "causal", "severity"):
            item = uncertainty.get(kind)
            if not isinstance(item, dict) or item.get("status") not in UNCERTAINTY_STATUSES:
                raise FoodLineHistoricalRecoveryError(f"cluster {index} {kind}_uncertainty is invalid")
            if item.get("status") == "unresolved" and not _text(item.get("note")):
                raise FoodLineHistoricalRecoveryError(f"cluster {index} unresolved {kind}_uncertainty requires a note")
            normalized_uncertainty[kind] = {"status": item["status"], "note": _text(item.get("note"))}
        consequence = submitted.get("measured_access_consequence")
        if not isinstance(consequence, dict):
            raise FoodLineHistoricalRecoveryError(f"cluster {index} measured_access_consequence must be an object")
        consequence_type = str(consequence.get("type") or "")
        if consequence_type not in CONSEQUENCE_PRIORITIES:
            raise FoodLineHistoricalRecoveryError(f"cluster {index} consequence type is unsupported")
        consequence_finding_ids = sorted(set(str(value) for value in consequence.get("supporting_finding_ids") or []))
        if any(value not in finding_ids for value in consequence_finding_ids):
            raise FoodLineHistoricalRecoveryError(f"cluster {index} consequence evidence is outside the cluster")
        if consequence_type != "risk_or_mitigation_only" and not _text(consequence.get("description")):
            raise FoodLineHistoricalRecoveryError(f"cluster {index} actual consequence requires a description")

        prior_match = submitted.get("prior_publication_match") or {"status": "none"}
        if not isinstance(prior_match, dict):
            raise FoodLineHistoricalRecoveryError(f"cluster {index} prior_publication_match must be an object")
        prior_status = str(prior_match.get("status") or "none")
        source_rows = [findings[value] for value in finding_ids]
        public_exact_match = any(
            reconciliation_by_url.get(item["canonical_source_url"], {}).get("published_exact_url_match")
            for item in source_rows
            if item["canonical_source_url"]
        )
        if prior_status == "exact_source_url" and not public_exact_match:
            raise FoodLineHistoricalRecoveryError(f"cluster {index} claims an exact public URL match that was not found")
        if prior_status == "event_identity":
            required_basis = {"location", "organization", "event_date_or_period", "pressure_type", "underlying_development"}
            if set(prior_match.get("match_basis") or []) != required_basis:
                raise FoodLineHistoricalRecoveryError(f"cluster {index} event-identity match basis is incomplete")
            if not _reference_exists(root, pages_root, str(prior_match.get("artifact_path") or "")):
                raise FoodLineHistoricalRecoveryError(f"cluster {index} event-identity artifact does not exist")
            submitted_identity = prior_match.get("matched_event_identity")
            if not isinstance(submitted_identity, dict) or {
                key: _identity_text(submitted_identity.get(key))
                for key in ("location", "organization", "pressure_category", "underlying_development")
            } != {
                key: identity[key]
                for key in ("location", "organization", "pressure_category", "underlying_development")
            } or str(submitted_identity.get("event_date_or_period") or "") != f"{event_start}/{event_end}":
                raise FoodLineHistoricalRecoveryError(f"cluster {index} event-identity match does not bind this event")
        elif prior_status not in {"none", "exact_source_url", "existing_private_record"}:
            raise FoodLineHistoricalRecoveryError(f"cluster {index} prior-publication match status is unsupported")

        if disposition == "already_published" and prior_status not in {"exact_source_url", "event_identity"}:
            raise FoodLineHistoricalRecoveryError(f"cluster {index} already_published lacks a verified public match")
        if disposition == "confirmed_historical_review_candidate":
            if prior_status != "none" or public_exact_match:
                raise FoodLineHistoricalRecoveryError(f"cluster {index} confirmed candidate overlaps an existing public event")
            if normalized_uncertainty["condition"]["status"] != "resolved":
                raise FoodLineHistoricalRecoveryError(f"cluster {index} unresolved condition uncertainty blocks confirmation")
            if consequence_type == "risk_or_mitigation_only":
                raise FoodLineHistoricalRecoveryError(f"cluster {index} risk-only development cannot be confirmed")
            if not consequence_finding_ids:
                raise FoodLineHistoricalRecoveryError(f"cluster {index} confirmed consequence lacks source support")
        if disposition == "deferred_specific_evidence_gap" and not _text(submitted.get("unresolved_requirement")):
            raise FoodLineHistoricalRecoveryError(f"cluster {index} deferred disposition requires unresolved_requirement")
        if disposition == "excluded_under_existing_rules" and not _text(submitted.get("exclusion_rule")):
            raise FoodLineHistoricalRecoveryError(f"cluster {index} excluded disposition requires exclusion_rule")

        normalized_clusters.append(
            {
                "event_id": event_id,
                "event_fingerprint": event_fingerprint,
                **identity,
                "location_display": _text(submitted.get("location")),
                "organization_display": _text(submitted.get("organization")),
                "underlying_development_display": _text(submitted.get("underlying_development")),
                "affected_population": sorted({_text(value) for value in submitted.get("affected_population") or [] if _text(value)}),
                "finding_ids": finding_ids,
                "primary_finding_id": primary,
                "sources": [
                    {
                        "finding_id": item["finding_id"],
                        "canonical_source_url": item["canonical_source_url"],
                        "publisher": item["publisher"],
                        "source_published_at": item["source_published_at"],
                        "discovered_at": item["completed_at"] or item["started_at"],
                        "exact_supporting_passage": item["exact_supporting_passage"],
                    }
                    for item in source_rows
                ],
                "measured_access_consequence": {
                    "type": consequence_type,
                    "description": _text(consequence.get("description")),
                    "measurement": _text(consequence.get("measurement")),
                    "supporting_finding_ids": consequence_finding_ids,
                },
                "uncertainty": normalized_uncertainty,
                "prior_publication_match": prior_match,
                "proposed_disposition": disposition,
                "disposition_reason": disposition_reason,
                "unresolved_requirement": _text(submitted.get("unresolved_requirement")) or None,
                "exclusion_rule": _text(submitted.get("exclusion_rule")) or None,
                "priority": CONSEQUENCE_PRIORITIES[consequence_type],
            }
        )
        assigned.extend(finding_ids)

    duplicate_assignments = sorted(value for value, count in Counter(assigned).items() if count > 1)
    missing_assignments = sorted(set(findings) - set(assigned))
    if duplicate_assignments or missing_assignments:
        raise FoodLineHistoricalRecoveryError(
            "cluster membership must assign every retained finding exactly once; "
            f"duplicates={duplicate_assignments} missing={missing_assignments}"
        )
    return sorted(normalized_clusters, key=lambda item: item["event_id"])


def build_recovery(
    root: Path,
    input_path: Path,
    cluster_spec_path: Path,
    *,
    pages_root: Path | None,
    captured_at: str,
    run_month: str | None = None,
) -> dict[str, Any]:
    raw = input_path.read_bytes()
    parsed = parse_aggregate_handoff(raw, run_month=run_month)
    captured_at = _timestamp(captured_at, "captured_at")
    try:
        spec = json.loads(cluster_spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FoodLineHistoricalRecoveryError(f"invalid cluster spec: {exc}") from exc
    if not isinstance(spec, dict):
        raise FoodLineHistoricalRecoveryError("cluster spec must be a JSON object")
    current_recovery = _validated_current_recovery_identity(root, parsed["input_sha256"])
    reconciliation = _build_reconciliation(
        current_recovery.repository_root,
        pages_root,
        parsed["sources"],
        current_recovery=current_recovery,
    )
    reconciliation["input_sha256"] = parsed["input_sha256"]
    reconciliation["run_month"] = parsed["run_month"]
    clusters = validate_clusters(spec, parsed, reconciliation, root=root, pages_root=pages_root)
    disposition_counts = Counter(item["proposed_disposition"] for item in clusters)
    confirmed = sorted(
        (item for item in clusters if item["proposed_disposition"] == "confirmed_historical_review_candidate"),
        key=lambda item: (item["priority"], item["event_start_date"], item["event_id"]),
    )
    raw_archive = {
        "schema_version": RAW_SCHEMA,
        "domain": "food-line",
        "input_sha256": parsed["input_sha256"],
        "run_month": parsed["run_month"],
        "input_filename": input_path.name,
        "captured_at": captured_at,
        "raw_text": raw.decode("utf-8", errors="replace"),
        "raw_bytes_base64": base64.b64encode(raw).decode("ascii"),
    }
    normalized_sources = {
        "schema_version": "food_line_historical_unique_sources_v1",
        "input_sha256": parsed["input_sha256"],
        "run_month": parsed["run_month"],
        "finding_count": parsed["retained_finding_count"],
        "unique_canonical_source_count": parsed["unique_canonical_source_count"],
        "sources": parsed["sources"],
    }
    event_manifest = {
        "schema_version": "food_line_historical_event_clusters_v1",
        "input_sha256": parsed["input_sha256"],
        "run_month": parsed["run_month"],
        "cluster_spec_sha256": sha256_bytes(cluster_spec_path.read_bytes()),
        "reviewed_by": spec["reviewed_by"],
        "reviewed_at": _timestamp(spec["reviewed_at"], "cluster spec reviewed_at"),
        "publication_approval": False,
        "cluster_count": len(clusters),
        "clusters": clusters,
    }
    disposition_matrix = {
        "schema_version": "food_line_historical_dispositions_v1",
        "input_sha256": parsed["input_sha256"],
        "run_month": parsed["run_month"],
        "counts": {value: disposition_counts.get(value, 0) for value in sorted(DISPOSITIONS)},
        "events": [
            {
                "event_id": item["event_id"],
                "event_fingerprint": item["event_fingerprint"],
                "proposed_disposition": item["proposed_disposition"],
                "disposition_reason": item["disposition_reason"],
                "unresolved_requirement": item["unresolved_requirement"],
                "exclusion_rule": item["exclusion_rule"],
            }
            for item in clusters
        ],
    }
    validation_report = {
        "schema_version": "food_line_historical_recovery_validation_v1",
        "input_sha256": parsed["input_sha256"],
        "run_month": parsed["run_month"],
        "valid": True,
        "publication_approval": False,
        **{key: parsed[key] for key in (
            "fence_count",
            "valid_json_block_count",
            "malformed_json_block_count",
            "invalid_envelope_block_count",
            "raw_finding_count",
            "out_of_scope_finding_count",
            "retained_finding_count",
            "duplicate_finding_occurrence_count",
            "unique_canonical_source_count",
            "malformed_blocks",
            "invalid_envelopes",
            "invalid_findings",
            "out_of_scope_findings",
        )},
        "event_cluster_count": len(clusters),
        "disposition_counts": disposition_matrix["counts"],
    }
    priority = {
        "schema_version": "food_line_historical_priority_candidates_v1",
        "input_sha256": parsed["input_sha256"],
        "run_month": parsed["run_month"],
        "publication_approval": False,
        "confirmed_candidate_count": len(confirmed),
        "candidates": [
            {
                "priority": item["priority"],
                "event_id": item["event_id"],
                "event_fingerprint": item["event_fingerprint"],
                "location": item["location_display"],
                "organization": item["organization_display"],
                "pressure_category": item["pressure_category"],
                "measured_access_consequence": item["measured_access_consequence"],
            }
            for item in confirmed
        ],
    }
    return {
        "raw_archive.json": raw_archive,
        "normalized_unique_sources.json": normalized_sources,
        "normalized_findings.json": {
            "schema_version": "food_line_historical_normalized_findings_v1",
            "input_sha256": parsed["input_sha256"],
            "run_month": parsed["run_month"],
            "findings": parsed["findings"],
        },
        "event_cluster_manifest.json": event_manifest,
        "live_site_reconciliation_report.json": reconciliation,
        "disposition_matrix.json": disposition_matrix,
        "import_validation_report.json": validation_report,
        "priority_confirmed_candidates.json": priority,
    }


def _artifact_hashes(artifacts: dict[str, Any]) -> dict[str, str]:
    return {
        name: sha256_bytes(canonical_json(value).encode("utf-8"))
        for name, value in sorted(artifacts.items())
    }


def _recovery_directory(root: Path, input_sha256: str) -> Path:
    # The complete digest remains authoritative in the manifest. A bounded
    # digest prefix keeps artifact paths usable in standard Windows worktrees;
    # a prefix collision fails closed when the manifest is compared.
    return _validated_current_recovery_identity(root, input_sha256).target


def import_recovery(root: Path, artifacts: dict[str, Any], *, cluster_spec_sha256: str) -> dict[str, Any]:
    input_sha256 = artifacts["import_validation_report.json"]["input_sha256"]
    target = _recovery_directory(root, input_sha256)
    hashes = _artifact_hashes(artifacts)
    artifact_set_sha256 = _fingerprint(hashes)
    manifest = {
        "schema_version": RECOVERY_SCHEMA,
        "input_sha256": input_sha256,
        "cluster_spec_sha256": cluster_spec_sha256,
        "artifact_hashes": hashes,
        "artifact_set_sha256": artifact_set_sha256,
        "publication_approval": False,
        "public_output_written": False,
        "queue_items_created": 0,
    }
    if target.exists():
        manifest_path = target / "recovery_manifest.json"
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FoodLineHistoricalRecoveryError("existing recovery manifest is missing or invalid") from exc
        if existing != manifest:
            raise FoodLineHistoricalRecoveryError("refusing conflicting replay for an existing content-addressed recovery")
        for name, expected in hashes.items():
            path = target / name
            if not path.is_file() or sha256_bytes(path.read_bytes()) != expected:
                raise FoodLineHistoricalRecoveryError(f"existing recovery artifact drifted: {name}")
        return {
            "status": "idempotent_noop",
            "recovery_path": str(target),
            "artifact_set_sha256": artifact_set_sha256,
            "artifact_count": len(artifacts) + 1,
            "would_write": False,
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=input_sha256[:16] + ".", dir=target.parent))
    try:
        for name, value in artifacts.items():
            _atomic_json(temporary / name, value)
        _atomic_json(temporary / "recovery_manifest.json", manifest)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "status": "imported",
        "recovery_path": str(target),
        "artifact_set_sha256": artifact_set_sha256,
        "artifact_count": len(artifacts) + 1,
        "would_write": True,
    }


def dry_run_result(root: Path, artifacts: dict[str, Any], *, cluster_spec_sha256: str) -> dict[str, Any]:
    input_sha256 = artifacts["import_validation_report.json"]["input_sha256"]
    hashes = _artifact_hashes(artifacts)
    return {
        "status": "validated_dry_run",
        "input_sha256": input_sha256,
        "cluster_spec_sha256": cluster_spec_sha256,
        "artifact_set_sha256": _fingerprint(hashes),
        "artifact_count": len(artifacts) + 1,
        "recovery_path": str(_recovery_directory(root, input_sha256)),
        "counts": artifacts["import_validation_report.json"],
        "would_write": False,
        "publication_approval": False,
    }


def _validated_real_file(path: Path, label: str) -> tuple[Path, bytes]:
    if ".." in path.parts:
        raise FoodLineHistoricalRecoveryError(f"{label} must not use path traversal")
    absolute = path.absolute()
    if not absolute.is_file() or _is_reparse_point(absolute):
        raise FoodLineHistoricalRecoveryError(f"{label} must be an existing real file")
    try:
        resolved = absolute.resolve(strict=True)
    except OSError as exc:
        raise FoodLineHistoricalRecoveryError(f"{label} does not resolve") from exc
    if not _same_path(absolute, resolved):
        raise FoodLineHistoricalRecoveryError(f"{label} resolves through a path alias")
    try:
        return resolved, resolved.read_bytes()
    except OSError as exc:
        raise FoodLineHistoricalRecoveryError(f"unable to read {label}") from exc


def _load_validated_predecessor(
    root: Path,
    input_path: Path,
    cluster_spec_path: Path,
    *,
    expected_artifact_set_sha256: str,
    captured_at: str,
    run_month: str,
) -> tuple[_CurrentRecoveryIdentity, dict[str, Any], dict[str, Any]]:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_artifact_set_sha256):
        raise FoodLineHistoricalRecoveryError(
            "predecessor artifact-set identity must be sha256 followed by 64 lowercase hexadecimal characters"
        )
    _, input_bytes = _validated_real_file(input_path, "historical recovery input")
    input_sha256 = sha256_bytes(input_bytes)
    identity = _validated_current_recovery_identity(root, input_sha256)
    if not identity.target.exists():
        raise FoodLineHistoricalRecoveryError("content-addressed predecessor recovery does not exist")

    expected_names = set(RECOVERY_ARTIFACT_SCHEMAS) | {"recovery_manifest.json"}
    entries: dict[str, Path] = {}
    for entry in identity.target.iterdir():
        if _is_reparse_point(entry) or not entry.is_file():
            raise FoodLineHistoricalRecoveryError(f"predecessor contains an unsafe or non-file entry: {entry.name}")
        entries[entry.name] = entry
    if set(entries) != expected_names:
        missing = sorted(expected_names - set(entries))
        unexpected = sorted(set(entries) - expected_names)
        raise FoodLineHistoricalRecoveryError(
            f"predecessor file inventory drifted: missing={missing} unexpected={unexpected}"
        )

    try:
        manifest = json.loads(entries["recovery_manifest.json"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FoodLineHistoricalRecoveryError("predecessor recovery manifest is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != RECOVERY_SCHEMA:
        raise FoodLineHistoricalRecoveryError("predecessor recovery schema drifted")
    expected_manifest_fields = {
        "schema_version",
        "input_sha256",
        "cluster_spec_sha256",
        "artifact_hashes",
        "artifact_set_sha256",
        "publication_approval",
        "public_output_written",
        "queue_items_created",
    }
    if set(manifest) != expected_manifest_fields:
        raise FoodLineHistoricalRecoveryError("predecessor recovery manifest fields drifted")
    if manifest.get("input_sha256") != input_sha256:
        raise FoodLineHistoricalRecoveryError("predecessor recovery input hash drifted")
    artifact_hashes = manifest.get("artifact_hashes")
    if not isinstance(artifact_hashes, dict) or set(artifact_hashes) != set(RECOVERY_ARTIFACT_SCHEMAS):
        raise FoodLineHistoricalRecoveryError("predecessor artifact hash inventory drifted")
    if any(not re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in artifact_hashes.values()):
        raise FoodLineHistoricalRecoveryError("predecessor artifact hash is malformed")
    calculated_artifact_set = _fingerprint(dict(sorted(artifact_hashes.items())))
    if manifest.get("artifact_set_sha256") != calculated_artifact_set:
        raise FoodLineHistoricalRecoveryError("predecessor artifact-set identity is internally inconsistent")
    if calculated_artifact_set != expected_artifact_set_sha256:
        raise FoodLineHistoricalRecoveryError(
            "predecessor artifact-set identity does not match the requested migration"
        )
    if (
        manifest.get("publication_approval") is not False
        or manifest.get("public_output_written") is not False
        or manifest.get("queue_items_created") != 0
    ):
        raise FoodLineHistoricalRecoveryError("predecessor recovery carries publication or queue authority")

    artifacts: dict[str, Any] = {}
    for name, expected_schema in RECOVERY_ARTIFACT_SCHEMAS.items():
        raw = entries[name].read_bytes()
        if sha256_bytes(raw) != artifact_hashes[name]:
            raise FoodLineHistoricalRecoveryError(f"predecessor artifact hash drifted: {name}")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FoodLineHistoricalRecoveryError(f"predecessor artifact is invalid JSON: {name}") from exc
        if not isinstance(value, dict) or value.get("schema_version") != expected_schema:
            raise FoodLineHistoricalRecoveryError(f"predecessor artifact schema drifted: {name}")
        if value.get("input_sha256") != input_sha256:
            raise FoodLineHistoricalRecoveryError(f"predecessor artifact input hash drifted: {name}")
        artifacts[name] = value

    _, cluster_spec_bytes = _validated_real_file(cluster_spec_path, "historical recovery cluster specification")
    cluster_spec_sha256 = sha256_bytes(cluster_spec_bytes)
    if manifest.get("cluster_spec_sha256") != cluster_spec_sha256:
        raise FoodLineHistoricalRecoveryError("predecessor cluster-specification hash drifted")
    if artifacts["event_cluster_manifest.json"].get("cluster_spec_sha256") != cluster_spec_sha256:
        raise FoodLineHistoricalRecoveryError("event manifest cluster-specification hash drifted")
    try:
        cluster_spec = json.loads(cluster_spec_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FoodLineHistoricalRecoveryError("historical recovery cluster specification is invalid JSON") from exc
    if (
        not isinstance(cluster_spec, dict)
        or cluster_spec.get("schema_version") != SPEC_SCHEMA
        or cluster_spec.get("input_sha256") != input_sha256
        or cluster_spec.get("run_month") != run_month
    ):
        raise FoodLineHistoricalRecoveryError("cluster specification does not bind the requested predecessor")

    raw_archive = artifacts["raw_archive.json"]
    try:
        archived_input = base64.b64decode(raw_archive.get("raw_bytes_base64", ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise FoodLineHistoricalRecoveryError("predecessor raw archive does not contain valid source bytes") from exc
    if archived_input != input_bytes or sha256_bytes(archived_input) != input_sha256:
        raise FoodLineHistoricalRecoveryError("predecessor raw archive does not preserve the bound input")
    if raw_archive.get("raw_text") != input_bytes.decode("utf-8", errors="replace"):
        raise FoodLineHistoricalRecoveryError("predecessor raw archive decoded text drifted")
    if raw_archive.get("captured_at") != _timestamp(captured_at, "captured_at"):
        raise FoodLineHistoricalRecoveryError("predecessor captured_at drifted")
    if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", run_month or ""):
        raise FoodLineHistoricalRecoveryError("run_month must be YYYY-MM")
    for name, value in artifacts.items():
        if value.get("run_month") != run_month:
            raise FoodLineHistoricalRecoveryError(f"predecessor run-month drifted: {name}")

    validation = artifacts["import_validation_report.json"]
    sources = artifacts["normalized_unique_sources.json"]
    events = artifacts["event_cluster_manifest.json"]
    dispositions = artifacts["disposition_matrix.json"]
    candidates = artifacts["priority_confirmed_candidates.json"]
    if validation.get("retained_finding_count") != sources.get("finding_count"):
        raise FoodLineHistoricalRecoveryError("predecessor retained-finding totals are inconsistent")
    if validation.get("retained_finding_count") != len(artifacts["normalized_findings.json"].get("findings", [])):
        raise FoodLineHistoricalRecoveryError("predecessor normalized-finding inventory is inconsistent")
    if validation.get("unique_canonical_source_count") != sources.get("unique_canonical_source_count"):
        raise FoodLineHistoricalRecoveryError("predecessor unique-source totals are inconsistent")
    if validation.get("event_cluster_count") != events.get("cluster_count"):
        raise FoodLineHistoricalRecoveryError("predecessor event-cluster totals are inconsistent")
    if events.get("cluster_count") != len(events.get("clusters", [])):
        raise FoodLineHistoricalRecoveryError("predecessor event-cluster inventory is inconsistent")
    if validation.get("disposition_counts") != dispositions.get("counts"):
        raise FoodLineHistoricalRecoveryError("predecessor disposition totals are inconsistent")
    confirmed_count = dispositions["counts"].get("confirmed_historical_review_candidate")
    if candidates.get("confirmed_candidate_count") != confirmed_count:
        raise FoodLineHistoricalRecoveryError("predecessor confirmed-candidate totals are inconsistent")
    if candidates.get("confirmed_candidate_count") != len(candidates.get("candidates", [])):
        raise FoodLineHistoricalRecoveryError("predecessor confirmed-candidate inventory is inconsistent")
    event_ids = {row.get("event_id") for row in events["clusters"]}
    disposition_ids = {row.get("event_id") for row in dispositions.get("events", [])}
    confirmed_event_ids = {
        row.get("event_id")
        for row in events["clusters"]
        if row.get("proposed_disposition") == "confirmed_historical_review_candidate"
    }
    candidate_ids = {row.get("event_id") for row in candidates["candidates"]}
    if event_ids != disposition_ids or confirmed_event_ids != candidate_ids:
        raise FoodLineHistoricalRecoveryError("predecessor event identities are inconsistent across artifacts")
    if candidates.get("publication_approval") is not False or events.get("publication_approval") is not False:
        raise FoodLineHistoricalRecoveryError("predecessor artifacts carry publication authority")
    return identity, artifacts, manifest


def _priority_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(int(row.get("priority", -1)) for row in rows)
    return {str(priority): counts[priority] for priority in sorted(counts)}


def _validate_legacy_priority_six_event(
    row: dict[str, Any], *, candidate_event_ids: set[str]
) -> None:
    event_id = row.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise FoodLineHistoricalRecoveryError("legacy Priority 6 event is missing its event identity")
    if event_id in candidate_event_ids:
        raise FoodLineHistoricalRecoveryError("legacy Priority 6 event appears in the confirmed-candidate report")
    consequence = row.get("measured_access_consequence")
    if not isinstance(consequence, dict) or consequence.get("type") != "risk_or_mitigation_only":
        raise FoodLineHistoricalRecoveryError("legacy Priority 6 event is not risk-or-mitigation-only")
    disposition = row.get("proposed_disposition")
    if disposition not in {
        "deferred_specific_evidence_gap",
        "excluded_under_existing_rules",
        "duplicate_or_corroboration",
    }:
        raise FoodLineHistoricalRecoveryError("legacy Priority 6 event is not demonstrably non-candidate")
    if not _text(row.get("disposition_reason")):
        raise FoodLineHistoricalRecoveryError("legacy Priority 6 event lacks a disposition reason")
    unresolved_requirement = _text(row.get("unresolved_requirement"))
    exclusion_rule = _text(row.get("exclusion_rule"))
    if disposition == "deferred_specific_evidence_gap" and (
        not unresolved_requirement or exclusion_rule
    ):
        raise FoodLineHistoricalRecoveryError("legacy Priority 6 deferred event lacks bounded evidence-gap proof")
    if disposition == "excluded_under_existing_rules" and (
        not exclusion_rule or unresolved_requirement
    ):
        raise FoodLineHistoricalRecoveryError("legacy Priority 6 excluded event lacks bounded exclusion proof")
    if disposition == "duplicate_or_corroboration" and (unresolved_requirement or exclusion_rule):
        raise FoodLineHistoricalRecoveryError("legacy Priority 6 duplicate event has conflicting disposition fields")
    for field in (
        "candidate_eligible",
        "eligible_for_review",
        "historical_review_candidate",
        "publication_approval",
        "publication_authorized",
        "publication_eligible",
        "qualifies_for_review",
    ):
        if row.get(field) not in (None, False, 0, ""):
            raise FoodLineHistoricalRecoveryError(
                f"legacy Priority 6 event has conflicting eligibility or authority: {field}"
            )


def _audit_four_tier_semantic_diff(
    predecessor: dict[str, Any], successor: dict[str, Any]
) -> dict[str, dict[str, dict[str, int]]]:
    if set(predecessor) != set(RECOVERY_ARTIFACT_SCHEMAS) or set(successor) != set(RECOVERY_ARTIFACT_SCHEMAS):
        raise FoodLineHistoricalRecoveryError("migration artifact inventory changed")
    for name in set(RECOVERY_ARTIFACT_SCHEMAS) - {
        "event_cluster_manifest.json",
        "priority_confirmed_candidates.json",
    }:
        if predecessor[name] != successor[name]:
            raise FoodLineHistoricalRecoveryError(f"migration changed an unapproved artifact: {name}")

    predecessor_candidate_rows = predecessor["priority_confirmed_candidates.json"].get(
        "candidates"
    )
    successor_candidate_rows = successor["priority_confirmed_candidates.json"].get(
        "candidates"
    )
    if not isinstance(predecessor_candidate_rows, list) or not isinstance(successor_candidate_rows, list):
        raise FoodLineHistoricalRecoveryError("migration candidate rows are invalid")
    predecessor_candidate_ids = {
        row.get("event_id") for row in predecessor_candidate_rows if isinstance(row, dict)
    }
    successor_candidate_ids = {
        row.get("event_id") for row in successor_candidate_rows if isinstance(row, dict)
    }
    if predecessor_candidate_ids != successor_candidate_ids or None in predecessor_candidate_ids:
        raise FoodLineHistoricalRecoveryError("migration changed or invalidated candidate identities")

    transition: dict[str, dict[str, dict[str, int]]] = {}
    for name, row_key in (
        ("event_cluster_manifest.json", "clusters"),
        ("priority_confirmed_candidates.json", "candidates"),
    ):
        old_document = predecessor[name]
        new_document = successor[name]
        old_header = {key: value for key, value in old_document.items() if key != row_key}
        new_header = {key: value for key, value in new_document.items() if key != row_key}
        if old_header != new_header:
            raise FoodLineHistoricalRecoveryError(f"migration changed unapproved metadata: {name}")
        if old_document.get("publication_approval") is not False:
            raise FoodLineHistoricalRecoveryError(f"migration source carries publication authority: {name}")
        old_rows = old_document.get(row_key)
        new_rows = new_document.get(row_key)
        if not isinstance(old_rows, list) or not isinstance(new_rows, list) or len(old_rows) != len(new_rows):
            raise FoodLineHistoricalRecoveryError(f"migration changed row membership: {name}")
        for index, (old_row, new_row) in enumerate(zip(old_rows, new_rows, strict=True), start=1):
            if not isinstance(old_row, dict) or not isinstance(new_row, dict):
                raise FoodLineHistoricalRecoveryError(f"migration row is not an object: {name} row {index}")
            old_priority = old_row.get("priority")
            if old_priority == 6:
                if name != "event_cluster_manifest.json":
                    raise FoodLineHistoricalRecoveryError("legacy Priority 6 is forbidden in candidate rankings")
                _validate_legacy_priority_six_event(
                    old_row,
                    candidate_event_ids=predecessor_candidate_ids,
                )
                _validate_legacy_priority_six_event(
                    new_row,
                    candidate_event_ids=successor_candidate_ids,
                )
                if new_row.get("priority") != 6:
                    raise FoodLineHistoricalRecoveryError(
                        f"migration changed legacy Priority 6: {name} row {index}"
                    )
            elif old_priority not in {1, 2, 3, 4, 5}:
                raise FoodLineHistoricalRecoveryError(
                    f"migration priority transition is invalid: {name} row {index}"
                )
            expected_priority = 4 if old_priority == 5 else old_priority
            if new_row.get("priority") != expected_priority:
                raise FoodLineHistoricalRecoveryError(f"migration priority transition is invalid: {name} row {index}")
            consequence = old_row.get("measured_access_consequence")
            consequence_type = consequence.get("type") if isinstance(consequence, dict) else None
            if old_priority == 5 and consequence_type != "disaster_household_food_loss":
                raise FoodLineHistoricalRecoveryError("Tier 5 is not bound to disaster-related demonstrated loss")
            if consequence_type == "disaster_household_food_loss" and old_priority != 5:
                raise FoodLineHistoricalRecoveryError("five-tier predecessor has an unexpected disaster-loss priority")
            old_without_priority = {key: value for key, value in old_row.items() if key != "priority"}
            new_without_priority = {key: value for key, value in new_row.items() if key != "priority"}
            if old_without_priority != new_without_priority:
                raise FoodLineHistoricalRecoveryError(f"migration changed unapproved semantics: {name} row {index}")
        if any(row.get("priority") == 5 for row in new_rows):
            raise FoodLineHistoricalRecoveryError(f"migration retained Tier 5: {name}")
        if not any(row.get("priority") == 5 for row in old_rows):
            raise FoodLineHistoricalRecoveryError(f"five-tier predecessor has no Tier 5 rows: {name}")
        transition[name] = {
            "predecessor": _priority_counts(old_rows),
            "successor": _priority_counts(new_rows),
        }
    return transition


def _four_tier_successor(predecessor: dict[str, Any]) -> dict[str, Any]:
    successor = json.loads(canonical_json(predecessor))
    for name, row_key in (
        ("event_cluster_manifest.json", "clusters"),
        ("priority_confirmed_candidates.json", "candidates"),
    ):
        for row in successor[name][row_key]:
            if row.get("priority") == 5:
                row["priority"] = 4
    _audit_four_tier_semantic_diff(predecessor, successor)
    return successor


def _validated_migration_target(root: Path, successor_identity_sha256: str) -> Path:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", successor_identity_sha256):
        raise FoodLineHistoricalRecoveryError("successor identity is malformed")
    repository_root = root.absolute().resolve(strict=True)
    food_line_root = repository_root / "data" / "agent-history" / "food-line"
    migration_root = food_line_root / "recovery-migrations"
    target = migration_root / f"sha256-{successor_identity_sha256.removeprefix('sha256:')[:32]}"
    current = repository_root
    for component in ("data", "agent-history", "food-line", "recovery-migrations", target.name):
        current = current / component
        if current.exists() and _is_reparse_point(current):
            raise FoodLineHistoricalRecoveryError(f"migration path contains a symlink or junction: {current}")
    if food_line_root.is_dir():
        for candidate in food_line_root.iterdir():
            if (
                os.path.normcase(candidate.name) == os.path.normcase(migration_root.name)
                and candidate.name != migration_root.name
            ):
                raise FoodLineHistoricalRecoveryError("migration root uses a non-canonical case alias")
    if migration_root.is_dir():
        for candidate in migration_root.iterdir():
            if os.path.normcase(candidate.name) == os.path.normcase(target.name) and candidate.name != target.name:
                raise FoodLineHistoricalRecoveryError("successor target uses a non-canonical case alias")
    if target.parent != migration_root:
        raise FoodLineHistoricalRecoveryError("successor target escaped the migration root")
    if target.exists():
        if not target.is_dir() or not _same_path(target.resolve(strict=True), target):
            raise FoodLineHistoricalRecoveryError("successor target is not a real canonical directory")
    return target


def migrate_recovery_to_four_tiers(
    root: Path,
    input_path: Path,
    cluster_spec_path: Path,
    *,
    predecessor_artifact_set_sha256: str,
    implementation_source_commit: str,
    captured_at: str,
    run_month: str,
) -> dict[str, Any]:
    """Create an immutable four-tier successor from one validated five-tier recovery."""
    if not re.fullmatch(r"[0-9a-f]{40}", implementation_source_commit):
        raise FoodLineHistoricalRecoveryError(
            "implementation source commit must be exactly 40 lowercase hexadecimal characters"
        )
    predecessor_identity, predecessor, predecessor_manifest = _load_validated_predecessor(
        root,
        input_path,
        cluster_spec_path,
        expected_artifact_set_sha256=predecessor_artifact_set_sha256,
        captured_at=captured_at,
        run_month=run_month,
    )
    successor = _four_tier_successor(predecessor)
    transition = _audit_four_tier_semantic_diff(predecessor, successor)
    identity_payload = {
        "migration_schema_version": MIGRATION_SCHEMA,
        "recovery_schema_version": RECOVERY_SCHEMA,
        "input_sha256": predecessor_identity.input_sha256,
        "predecessor_artifact_set_sha256": predecessor_artifact_set_sha256,
        "predecessor_priority_policy_version": FIVE_TIER_PRIORITY_POLICY,
        "successor_priority_policy_version": FOUR_TIER_PRIORITY_POLICY,
        "successor_priority_semantics": FOUR_TIER_PRIORITY_SEMANTICS,
    }
    successor_identity_sha256 = _fingerprint(identity_payload)
    target = _validated_migration_target(root, successor_identity_sha256)
    hashes = _artifact_hashes(successor)
    artifact_set_sha256 = _fingerprint(hashes)
    predecessor_relative_path = predecessor_identity.target.relative_to(
        predecessor_identity.repository_root
    ).as_posix()
    validation = successor["import_validation_report.json"]
    manifest = {
        "schema_version": MIGRATION_SCHEMA,
        "successor_identity_sha256": successor_identity_sha256,
        "successor_identity": identity_payload,
        "input_sha256": predecessor_identity.input_sha256,
        "cluster_spec_sha256": predecessor_manifest["cluster_spec_sha256"],
        "captured_at": captured_at,
        "run_month": run_month,
        "implementation_source_commit": implementation_source_commit,
        "predecessor": {
            "path": predecessor_relative_path,
            "artifact_set_sha256": predecessor_artifact_set_sha256,
            "artifact_hashes": predecessor_manifest["artifact_hashes"],
        },
        "priority_policy_transition": {
            "from": FIVE_TIER_PRIORITY_POLICY,
            "to": FOUR_TIER_PRIORITY_POLICY,
            "semantics": FOUR_TIER_PRIORITY_SEMANTICS,
            "tier_counts": transition,
            "event_to_confirmed_tier_5_difference": {
                "count": (
                    transition["event_cluster_manifest.json"]["predecessor"].get("5", 0)
                    - transition["priority_confirmed_candidates.json"]["predecessor"].get("5", 0)
                ),
                "reason": (
                    "event manifest includes non-confirmed dispositions; "
                    "candidate report includes confirmed events only"
                ),
            },
        },
        "recovery_totals": {
            "retained_findings": validation["retained_finding_count"],
            "unique_canonical_urls": validation["unique_canonical_source_count"],
            "event_clusters": validation["event_cluster_count"],
            "dispositions": validation["disposition_counts"],
        },
        "artifact_hashes": hashes,
        "artifact_set_sha256": artifact_set_sha256,
        "publication_approval": False,
        "public_output_written": False,
        "queue_items_created": 0,
        "pages_files_written": 0,
        "generated_output_files_written": 0,
        "audio_files_written": 0,
        "social_posts_created": 0,
        "scheduled_tasks_changed": 0,
    }
    expected_names = set(RECOVERY_ARTIFACT_SCHEMAS) | {"recovery_manifest.json"}
    if target.exists():
        entries = list(target.iterdir())
        if any(_is_reparse_point(entry) or not entry.is_file() for entry in entries):
            raise FoodLineHistoricalRecoveryError("existing successor contains an unsafe or non-file entry")
        if {entry.name for entry in entries} != expected_names:
            raise FoodLineHistoricalRecoveryError("existing successor file inventory drifted")
        for name, value in {**successor, "recovery_manifest.json": manifest}.items():
            expected_bytes = canonical_json(value).encode("utf-8")
            if (target / name).read_bytes() != expected_bytes:
                raise FoodLineHistoricalRecoveryError(f"refusing conflicting migration replay: {name}")
        return {
            "status": "idempotent_noop",
            "recovery_path": str(target),
            "successor_identity_sha256": successor_identity_sha256,
            "artifact_set_sha256": artifact_set_sha256,
            "artifact_count": len(successor) + 1,
            "priority_transition": transition,
            "recovery_totals": manifest["recovery_totals"],
            "would_write": False,
            "publication_approval": False,
            "queue_items_created": 0,
            "pages_files_written": 0,
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    if _is_reparse_point(target.parent):
        raise FoodLineHistoricalRecoveryError("migration root became a symlink or junction")
    temporary = Path(tempfile.mkdtemp(prefix=target.name + ".", dir=target.parent))
    try:
        for name, value in successor.items():
            _atomic_json(temporary / name, value)
        _atomic_json(temporary / "recovery_manifest.json", manifest)
        os.replace(temporary, target)
    except OSError as exc:
        raise FoodLineHistoricalRecoveryError(f"atomic successor creation failed: {exc}") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "status": "migrated",
        "recovery_path": str(target),
        "successor_identity_sha256": successor_identity_sha256,
        "artifact_set_sha256": artifact_set_sha256,
        "artifact_count": len(successor) + 1,
        "priority_transition": transition,
        "recovery_totals": manifest["recovery_totals"],
        "would_write": True,
        "publication_approval": False,
        "queue_items_created": 0,
        "pages_files_written": 0,
    }


def validate_migration_implementation_commit(root: Path, source_commit: str) -> None:
    """Require the recorded commit to be protected-history code containing this owner."""
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise FoodLineHistoricalRecoveryError(
            "implementation source commit must be exactly 40 lowercase hexadecimal characters"
        )
    commands = (
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        ["git", "-C", str(root), "merge-base", "--is-ancestor", source_commit, "HEAD"],
        [
            "git",
            "-C",
            str(root),
            "show",
            f"{source_commit}:src/bluefern_dispatches/food_line_historical_recovery.py",
        ],
    )
    try:
        root_result = subprocess.run(commands[0], check=True, capture_output=True, text=True)
        if not _same_path(Path(root_result.stdout.strip()), root.resolve(strict=True)):
            raise FoodLineHistoricalRecoveryError("repository root does not bind the migration source commit")
        subprocess.run(commands[1], check=True, capture_output=True, text=True)
        source_result = subprocess.run(commands[2], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FoodLineHistoricalRecoveryError(
            "implementation source commit is not an ancestor containing the migration owner"
        ) from exc
    if "def migrate_recovery_to_four_tiers(" not in source_result.stdout:
        raise FoodLineHistoricalRecoveryError("implementation source commit does not contain the migration owner")


def _require_review_text(value: Any, field: str, *, maximum: int = 4000) -> str:
    text = _text(value)
    if not text:
        raise FoodLineHistoricalRecoveryError(f"{field} is required")
    if len(text) > maximum:
        raise FoodLineHistoricalRecoveryError(f"{field} exceeds {maximum} characters")
    return text


def _load_validated_four_tier_successor(
    root: Path,
    successor_identity_sha256: str,
    artifact_set_sha256: str,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Load one exact immutable four-tier successor without accepting an arbitrary path."""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact_set_sha256):
        raise FoodLineHistoricalRecoveryError("successor artifact-set identity is malformed")
    target = _validated_migration_target(root, successor_identity_sha256)
    if not target.is_dir():
        raise FoodLineHistoricalRecoveryError("requested four-tier successor does not exist")
    expected_names = set(RECOVERY_ARTIFACT_SCHEMAS) | {"recovery_manifest.json"}
    entries: dict[str, Path] = {}
    for entry in target.iterdir():
        if _is_reparse_point(entry) or not entry.is_file():
            raise FoodLineHistoricalRecoveryError(
                f"four-tier successor contains an unsafe or non-file entry: {entry.name}"
            )
        entries[entry.name] = entry
    if set(entries) != expected_names:
        raise FoodLineHistoricalRecoveryError("four-tier successor file inventory drifted")
    try:
        manifest = json.loads(entries["recovery_manifest.json"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FoodLineHistoricalRecoveryError("four-tier successor manifest is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != MIGRATION_SCHEMA:
        raise FoodLineHistoricalRecoveryError("four-tier successor manifest schema drifted")
    if manifest.get("successor_identity_sha256") != successor_identity_sha256:
        raise FoodLineHistoricalRecoveryError("four-tier successor identity does not match")
    if manifest.get("artifact_set_sha256") != artifact_set_sha256:
        raise FoodLineHistoricalRecoveryError("four-tier successor artifact-set identity does not match")
    transition = manifest.get("priority_policy_transition")
    if not isinstance(transition, dict) or transition.get("to") != FOUR_TIER_PRIORITY_POLICY:
        raise FoodLineHistoricalRecoveryError("successor is not governed by the four-tier policy")
    authority_guards = {
        "publication_approval": False,
        "public_output_written": False,
        "queue_items_created": 0,
        "pages_files_written": 0,
        "generated_output_files_written": 0,
        "audio_files_written": 0,
        "social_posts_created": 0,
        "scheduled_tasks_changed": 0,
    }
    if any(manifest.get(key) != expected for key, expected in authority_guards.items()):
        raise FoodLineHistoricalRecoveryError("four-tier successor carries publication or mutation authority")
    artifact_hashes = manifest.get("artifact_hashes")
    if not isinstance(artifact_hashes, dict) or set(artifact_hashes) != set(RECOVERY_ARTIFACT_SCHEMAS):
        raise FoodLineHistoricalRecoveryError("four-tier successor artifact hash inventory drifted")
    if _fingerprint(dict(sorted(artifact_hashes.items()))) != artifact_set_sha256:
        raise FoodLineHistoricalRecoveryError("four-tier successor artifact-set identity is inconsistent")
    artifacts: dict[str, Any] = {}
    input_sha256 = manifest.get("input_sha256")
    for name, schema in RECOVERY_ARTIFACT_SCHEMAS.items():
        raw = entries[name].read_bytes()
        if sha256_bytes(raw) != artifact_hashes.get(name):
            raise FoodLineHistoricalRecoveryError(f"four-tier successor artifact drifted: {name}")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FoodLineHistoricalRecoveryError(f"four-tier successor artifact is invalid: {name}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != schema:
            raise FoodLineHistoricalRecoveryError(f"four-tier successor artifact schema drifted: {name}")
        if payload.get("input_sha256") != input_sha256:
            raise FoodLineHistoricalRecoveryError(f"four-tier successor input lineage drifted: {name}")
        artifacts[name] = payload
    candidates = artifacts["priority_confirmed_candidates.json"]
    if candidates.get("publication_approval") is not False:
        raise FoodLineHistoricalRecoveryError("confirmed-candidate report carries publication authority")
    rows = candidates.get("candidates")
    if not isinstance(rows, list) or any(row.get("priority") not in {1, 2, 3, 4} for row in rows):
        raise FoodLineHistoricalRecoveryError("confirmed-candidate report is not limited to tiers 1 through 4")
    return target, manifest, artifacts


def _validated_clean_pages_head(pages_root: Path) -> tuple[Path, str]:
    absolute = pages_root.absolute()
    if not absolute.is_dir() or _is_reparse_point(absolute):
        raise FoodLineHistoricalRecoveryError("Pages root must be an existing real directory")
    resolved = absolute.resolve(strict=True)
    if not _same_path(absolute, resolved):
        raise FoodLineHistoricalRecoveryError("Pages root resolves through a path alias")
    try:
        top = subprocess.run(
            ["git", "-C", str(resolved), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        head = subprocess.run(
            ["git", "-C", str(resolved), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(resolved), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FoodLineHistoricalRecoveryError("Pages root is not a readable Git checkout") from exc
    if not _same_path(Path(top), resolved) or status.strip():
        raise FoodLineHistoricalRecoveryError("Pages checkout must be the clean repository root")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise FoodLineHistoricalRecoveryError("Pages HEAD is malformed")
    return resolved, head


def _validate_review_evidence(review: dict[str, Any], event: dict[str, Any]) -> list[dict[str, Any]]:
    event_sources = event.get("sources")
    evidence = review.get("evidence_references")
    if not isinstance(event_sources, list) or not event_sources:
        raise FoodLineHistoricalRecoveryError("recovered event has no source evidence")
    if not isinstance(evidence, list) or not evidence:
        raise FoodLineHistoricalRecoveryError("review evidence_references must be non-empty")
    source_by_url = {
        normalize_source_url(str(row.get("canonical_source_url") or "")): row
        for row in event_sources
        if isinstance(row, dict) and row.get("canonical_source_url")
    }
    validated: list[dict[str, Any]] = []
    principal = 0
    for index, row in enumerate(evidence):
        if not isinstance(row, dict) or set(row) != {
            "canonical_source_url",
            "publisher",
            "source_published_at",
            "role",
            "exact_supporting_passages",
        }:
            raise FoodLineHistoricalRecoveryError(f"evidence_references[{index}] fields are invalid")
        url = normalize_source_url(_require_review_text(row.get("canonical_source_url"), "evidence URL", maximum=2000))
        source = source_by_url.get(url)
        if source is None:
            raise FoodLineHistoricalRecoveryError("review evidence URL is not bound to the recovered event")
        if row.get("publisher") != source.get("publisher") or row.get("source_published_at") != source.get("source_published_at"):
            raise FoodLineHistoricalRecoveryError("review evidence publisher or publication date drifted")
        if row.get("role") not in {"principal", "corroborating"}:
            raise FoodLineHistoricalRecoveryError("review evidence role is invalid")
        principal += row.get("role") == "principal"
        passages = row.get("exact_supporting_passages")
        if not isinstance(passages, list) or not passages:
            raise FoodLineHistoricalRecoveryError("review evidence requires exact supporting passages")
        cleaned = [_require_review_text(value, "supporting passage") for value in passages]
        preserved = _text(source.get("exact_supporting_passage"))
        if preserved and preserved not in cleaned:
            raise FoodLineHistoricalRecoveryError("review evidence omits the preserved recovery passage")
        validated.append({**row, "canonical_source_url": url, "exact_supporting_passages": cleaned})
    if not principal:
        raise FoodLineHistoricalRecoveryError("review requires at least one principal source")
    return validated


def _validate_publication_copy(copy: Any, batch: Any, event: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(copy, dict) or set(copy) != {"headline", "summary", "source_links"}:
        raise FoodLineHistoricalRecoveryError("confirmed review publication_copy fields are invalid")
    headline = _require_review_text(copy.get("headline"), "publication headline", maximum=180)
    summary = _require_review_text(copy.get("summary"), "publication summary", maximum=1200)
    source_links = copy.get("source_links")
    allowed_links = {
        normalize_source_url(str(row.get("canonical_source_url") or ""))
        for row in event.get("sources") or []
        if isinstance(row, dict)
    }
    if not isinstance(source_links, list) or not source_links:
        raise FoodLineHistoricalRecoveryError("confirmed review requires source links")
    normalized_links = [normalize_source_url(_require_review_text(value, "source link", maximum=2000)) for value in source_links]
    if any(value not in allowed_links for value in normalized_links):
        raise FoodLineHistoricalRecoveryError("publication copy contains a source link outside the recovered event")
    public_text = f"{headline} {summary}"
    if re.search(r"food-line-event-|sha256:|\b20\d{2}-\d{2}-\d{2}\b|\|", public_text, re.I):
        raise FoodLineHistoricalRecoveryError("publication copy exposes internal identity, ISO date, or pipe metadata")
    if not isinstance(batch, dict) or set(batch) != {"batch_id", "order", "edition_title", "edition_introduction"}:
        raise FoodLineHistoricalRecoveryError("confirmed review recommended_batch fields are invalid")
    batch_id = _require_review_text(batch.get("batch_id"), "batch_id", maximum=100)
    if not re.fullmatch(r"food-line-august-2026-retrospective-[0-9]{2}", batch_id):
        raise FoodLineHistoricalRecoveryError("batch_id is outside the sanctioned August 2026 retrospective namespace")
    order = batch.get("order")
    if not isinstance(order, int) or isinstance(order, bool) or not 1 <= order <= 6:
        raise FoodLineHistoricalRecoveryError("batch order must honor the six-story Food Line cap")
    edition_title = _require_review_text(batch.get("edition_title"), "edition title", maximum=180)
    edition_introduction = _require_review_text(batch.get("edition_introduction"), "edition introduction", maximum=1200)
    if re.search(r"food-line-event-|sha256:|\b20\d{2}-\d{2}-\d{2}\b|\|", f"{edition_title} {edition_introduction}", re.I):
        raise FoodLineHistoricalRecoveryError("batch copy exposes internal identity, ISO date, or pipe metadata")
    return (
        {"headline": headline, "summary": summary, "source_links": normalized_links},
        {
            "batch_id": batch_id,
            "order": order,
            "edition_title": edition_title,
            "edition_introduction": edition_introduction,
        },
    )


def _validated_review_decision_path(
    repository_root: Path,
    successor_identity_sha256: str,
    event_id: str,
) -> Path:
    relative_parts = (
        "data",
        "agent-history",
        "food-line",
        "reviews",
        "recovery-decisions",
        successor_identity_sha256.removeprefix("sha256:")[:32],
    )
    current = repository_root
    for component in relative_parts:
        current = current / component
        if current.exists() and _is_reparse_point(current):
            raise FoodLineHistoricalRecoveryError(
                f"historical review decision path contains a symlink or junction: {current}"
            )
    decision_path = current / f"{event_id}.json"
    if decision_path.exists() and (
        not decision_path.is_file() or _is_reparse_point(decision_path)
    ):
        raise FoodLineHistoricalRecoveryError("historical review decision target is unsafe")
    return decision_path


def record_historical_event_review(
    root: Path,
    pages_root: Path,
    *,
    successor_identity_sha256: str,
    artifact_set_sha256: str,
    event_id: str,
    decision: str,
    review_artifact_path: Path,
    review_artifact_sha256: str,
    operator: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Record one deterministic, non-authorizing review of a migrated event cluster."""
    if decision not in HISTORICAL_EVENT_DECISIONS:
        raise FoodLineHistoricalRecoveryError("unsupported historical event review decision")
    if not re.fullmatch(r"food-line-event-[0-9a-f]{24}", event_id):
        raise FoodLineHistoricalRecoveryError("event_id is malformed")
    if not re.fullmatch(r"[0-9a-f]{64}", review_artifact_sha256):
        raise FoodLineHistoricalRecoveryError("review artifact SHA-256 is malformed")
    operator = _require_review_text(operator, "operator", maximum=200)
    repository_root = root.absolute().resolve(strict=True)
    target, _manifest, artifacts = _load_validated_four_tier_successor(
        repository_root, successor_identity_sha256, artifact_set_sha256
    )
    pages_repository, pages_head = _validated_clean_pages_head(pages_root)
    review_path, review_bytes = _validated_real_file(review_artifact_path, "historical event review artifact")
    reviews_root = (repository_root / "data" / "agent-history" / "food-line" / "reviews").resolve()
    submissions_root = reviews_root / "recovery-submissions"
    try:
        review_path.relative_to(submissions_root)
    except ValueError as exc:
        raise FoodLineHistoricalRecoveryError(
            "review artifact must be under the private recovery-submissions directory"
        ) from exc
    if sha256_bytes(review_bytes) != review_artifact_sha256:
        raise FoodLineHistoricalRecoveryError("review artifact SHA-256 mismatch")
    try:
        review = json.loads(review_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FoodLineHistoricalRecoveryError("review artifact is not valid UTF-8 JSON") from exc
    if not isinstance(review, dict) or review.get("schema_version") != HISTORICAL_EVENT_REVIEW_SCHEMA:
        raise FoodLineHistoricalRecoveryError("historical event review schema is invalid")
    expected_review_fields = {
        "schema_version", "review_type", "source_head", "pages_head", "recovery_identity_sha256",
        "recovery_artifact_set_sha256", "event_id", "event_fingerprint", "decision",
        "decision_reason", "reviewed_by", "reviewed_at", "evidence_references", "event_assessment",
        "dedupe_assessment", "publication_copy", "recommended_batch", "archive_mutation_authorized",
        "intake_authorized", "queue_authorized", "generation_authorized", "approval_authorized",
        "publication_authorized", "pages_authorized", "audio_authorized", "social_authorized",
        "scheduled_task_change_authorized",
    }
    if set(review) != expected_review_fields or review.get("review_type") != "historical_event_editorial_review":
        raise FoodLineHistoricalRecoveryError("historical event review fields are invalid")
    if any(
        review.get(key) is not False
        for key in (
            "archive_mutation_authorized", "intake_authorized", "queue_authorized",
            "generation_authorized", "approval_authorized", "publication_authorized",
            "pages_authorized", "audio_authorized", "social_authorized",
            "scheduled_task_change_authorized",
        )
    ):
        raise FoodLineHistoricalRecoveryError("historical event review cannot grant mutation or publication authority")
    try:
        source_head = subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FoodLineHistoricalRecoveryError("unable to bind source HEAD") from exc
    identity_values = {
        "source_head": source_head,
        "pages_head": pages_head,
        "recovery_identity_sha256": successor_identity_sha256,
        "recovery_artifact_set_sha256": artifact_set_sha256,
        "event_id": event_id,
        "decision": decision,
    }
    if any(review.get(key) != value for key, value in identity_values.items()):
        raise FoodLineHistoricalRecoveryError("review source, Pages, recovery, event, or decision binding drifted")
    _require_review_text(review.get("decision_reason"), "decision_reason", maximum=2000)
    _require_review_text(review.get("reviewed_by"), "reviewed_by", maximum=200)
    _timestamp(review.get("reviewed_at"), "reviewed_at")

    events = artifacts["event_cluster_manifest.json"].get("clusters")
    matches = [row for row in events or [] if isinstance(row, dict) and row.get("event_id") == event_id]
    candidates = artifacts["priority_confirmed_candidates.json"].get("candidates")
    candidate_matches = [row for row in candidates or [] if isinstance(row, dict) and row.get("event_id") == event_id]
    if len(matches) != 1 or len(candidate_matches) != 1:
        raise FoodLineHistoricalRecoveryError("event must resolve once in both event and confirmed-candidate manifests")
    event = matches[0]
    candidate = candidate_matches[0]
    if event.get("proposed_disposition") != "confirmed_historical_review_candidate":
        raise FoodLineHistoricalRecoveryError("event is not a confirmed private review candidate")
    if review.get("event_fingerprint") != event.get("event_fingerprint") or candidate.get("event_fingerprint") != event.get("event_fingerprint"):
        raise FoodLineHistoricalRecoveryError("event fingerprint lineage drifted")
    evidence = _validate_review_evidence(review, event)

    assessment = review.get("event_assessment")
    if not isinstance(assessment, dict) or set(assessment) != {
        "location", "organization", "affected_population", "event_start_date", "event_end_date",
        "service", "measurable_change", "attribution", "uncertainty",
    }:
        raise FoodLineHistoricalRecoveryError("event_assessment fields are invalid")
    exact_assessment = {
        "location": event.get("location_display"),
        "organization": event.get("organization_display"),
        "affected_population": event.get("affected_population"),
        "event_start_date": event.get("event_start_date"),
        "event_end_date": event.get("event_end_date"),
        "measurable_change": event.get("measured_access_consequence"),
        "uncertainty": event.get("uncertainty"),
    }
    if any(assessment.get(key) != value for key, value in exact_assessment.items()):
        raise FoodLineHistoricalRecoveryError("review event facts or uncertainty drifted from the recovered event")
    _require_review_text(assessment.get("service"), "event service", maximum=500)
    _require_review_text(assessment.get("attribution"), "event attribution", maximum=1200)

    dedupe = review.get("dedupe_assessment")
    if not isinstance(dedupe, dict) or set(dedupe) != {
        "result", "checked_surfaces", "matched_reference", "continued_condition_assessment"
    }:
        raise FoodLineHistoricalRecoveryError("dedupe_assessment fields are invalid")
    if set(dedupe.get("checked_surfaces") or []) != HISTORICAL_DEDUPE_SURFACES or len(dedupe["checked_surfaces"]) != len(HISTORICAL_DEDUPE_SURFACES):
        raise FoodLineHistoricalRecoveryError("dedupe assessment did not cover every required surface")
    _require_review_text(dedupe.get("continued_condition_assessment"), "continued-condition assessment", maximum=1200)
    expected_dedupe = {
        "confirmed": {"no_match"},
        "deferred_specific_evidence_gap": {"no_match"},
        "excluded_under_existing_rules": {"no_match"},
        "duplicate_or_corroboration": {"duplicate_existing_event", "corroborating_source"},
        "already_published": {"already_published"},
    }[decision]
    if dedupe.get("result") not in expected_dedupe:
        raise FoodLineHistoricalRecoveryError("dedupe result does not match the editorial decision")
    matched_reference = dedupe.get("matched_reference")
    if dedupe.get("result") == "no_match":
        if matched_reference is not None:
            raise FoodLineHistoricalRecoveryError("no-match dedupe result cannot carry a matched reference")
    else:
        if not isinstance(matched_reference, dict) or set(matched_reference) != {
            "repository", "artifact_path", "reference_id", "event_fingerprint", "canonical_source_url"
        }:
            raise FoodLineHistoricalRecoveryError("matched dedupe reference fields are invalid")
        repository = matched_reference.get("repository")
        base = repository_root if repository == "source" else pages_repository if repository == "pages" else None
        if base is None:
            raise FoodLineHistoricalRecoveryError("matched dedupe reference repository is invalid")
        relative = Path(_require_review_text(matched_reference.get("artifact_path"), "matched artifact path", maximum=1000))
        if relative.is_absolute() or ".." in relative.parts:
            raise FoodLineHistoricalRecoveryError("matched dedupe artifact path must be repository-relative")
        unresolved_path = base
        for component in relative.parts:
            unresolved_path = unresolved_path / component
            if unresolved_path.exists() and _is_reparse_point(unresolved_path):
                raise FoodLineHistoricalRecoveryError("matched dedupe reference uses a symlink or junction")
        matched_path = unresolved_path.resolve(strict=True)
        try:
            matched_path.relative_to(base)
        except ValueError as exc:
            raise FoodLineHistoricalRecoveryError("matched dedupe reference escaped its repository") from exc
        if not matched_path.is_file() or _is_reparse_point(matched_path):
            raise FoodLineHistoricalRecoveryError("matched dedupe reference is not a real file")
        _require_review_text(matched_reference.get("reference_id"), "matched reference ID", maximum=300)
        matched_fingerprint = _require_review_text(
            matched_reference.get("event_fingerprint"), "matched event fingerprint", maximum=100
        )
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", matched_fingerprint):
            raise FoodLineHistoricalRecoveryError("matched event fingerprint is malformed")
        normalize_source_url(_require_review_text(matched_reference.get("canonical_source_url"), "matched source URL", maximum=2000))

    if decision == "confirmed":
        if (assessment.get("uncertainty") or {}).get("condition", {}).get("status") != "resolved":
            raise FoodLineHistoricalRecoveryError("confirmed review requires a resolved demonstrated condition")
        publication_copy, recommended_batch = _validate_publication_copy(
            review.get("publication_copy"), review.get("recommended_batch"), event
        )
    else:
        if review.get("publication_copy") is not None or review.get("recommended_batch") is not None:
            raise FoodLineHistoricalRecoveryError("non-confirmed review cannot prepare publication copy or a batch slot")
        publication_copy, recommended_batch = None, None

    decision_path = _validated_review_decision_path(
        repository_root, successor_identity_sha256, event_id
    )
    immutable = {
        "schema_version": HISTORICAL_EVENT_DECISION_SCHEMA,
        "domain": "food-line",
        "source_head": source_head,
        "pages_head": pages_head,
        "recovery_path": target.relative_to(repository_root).as_posix(),
        "recovery_identity_sha256": successor_identity_sha256,
        "recovery_artifact_set_sha256": artifact_set_sha256,
        "review_artifact_path": review_path.relative_to(repository_root).as_posix(),
        "review_artifact_sha256": review_artifact_sha256,
        "operator": operator,
        "reviewed_by": review["reviewed_by"],
        "reviewed_at": review["reviewed_at"],
        "event_id": event_id,
        "event_fingerprint": event["event_fingerprint"],
        "priority": candidate["priority"],
        "decision": decision,
        "decision_reason": review["decision_reason"],
        "evidence_references": evidence,
        "event_assessment": assessment,
        "dedupe_assessment": dedupe,
        "publication_copy": publication_copy,
        "recommended_batch": recommended_batch,
        "publication_eligible": False,
        "publication_approval": False,
        "archive_mutation_authorized": False,
        "intake_authorized": False,
        "queue_authorized": False,
        "generation_authorized": False,
        "approval_authorized": False,
        "publication_authorized": False,
        "pages_authorized": False,
        "audio_authorized": False,
        "social_authorized": False,
        "scheduled_task_change_authorized": False,
    }
    if decision_path.exists():
        if decision_path.read_bytes() != canonical_json(immutable).encode("utf-8"):
            raise FoodLineHistoricalRecoveryError("refusing a conflicting historical event decision replay")
        status = "idempotent_noop"
    elif dry_run:
        status = "dry_run_validated"
    else:
        _atomic_json(decision_path, immutable)
        status = "decision_recorded"
    return {
        "status": status,
        "event_id": event_id,
        "decision": decision,
        "decision_path": str(decision_path),
        "persistent_mutation": status == "decision_recorded",
        "publication_approval": False,
        "queue_items_created": 0,
        "pages_files_written": 0,
        "generated_output_files_written": 0,
    }
