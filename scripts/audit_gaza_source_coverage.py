from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DISPATCH_SLUG = "gaza"
OUTPUT_DIR = ROOT / "output" / "review" / DISPATCH_SLUG


TARGET_SOURCE_UNIVERSE: list[dict[str, Any]] = [
    {
        "target_name": "OCHA oPt",
        "source_id_candidates": ["ocha-opt-updates"],
        "publisher_candidates": ["OCHA", "OCHA oPt"],
        "coverage_category": "official_humanitarian",
        "reliability_role": "official_humanitarian",
        "risk_if_missing": "Loss of official humanitarian coordination and access signals.",
        "manual_backfill_required": False,
        "core_target": True,
    },
    {
        "target_name": "UNRWA",
        "source_id_candidates": ["unrwa-updates"],
        "publisher_candidates": ["UNRWA"],
        "coverage_category": "official_humanitarian",
        "reliability_role": "official_humanitarian",
        "risk_if_missing": "Loss of UNRWA response and access context.",
        "manual_backfill_required": True,
        "core_target": True,
    },
    {
        "target_name": "WHO",
        "source_id_candidates": ["who-news"],
        "publisher_candidates": ["WHO", "World Health Organization"],
        "coverage_category": "official_humanitarian",
        "reliability_role": "official_humanitarian",
        "risk_if_missing": "Loss of official health-system and outbreak context.",
        "manual_backfill_required": False,
        "core_target": True,
    },
    {
        "target_name": "UNICEF",
        "source_id_candidates": ["unicef-press-releases"],
        "publisher_candidates": ["UNICEF"],
        "coverage_category": "official_humanitarian",
        "reliability_role": "official_humanitarian",
        "risk_if_missing": "Loss of official child-protection and aid signals.",
        "manual_backfill_required": True,
        "core_target": True,
    },
    {
        "target_name": "WFP",
        "source_id_candidates": ["wfp-newsroom"],
        "publisher_candidates": ["WFP", "World Food Programme"],
        "coverage_category": "official_humanitarian",
        "reliability_role": "official_humanitarian",
        "risk_if_missing": "Loss of food-access and distribution signals.",
        "manual_backfill_required": True,
        "core_target": True,
    },
    {
        "target_name": "PRCS / Palestinian Red Crescent",
        "source_id_candidates": ["prcs-updates", "palestinian-red-crescent", "palestine-red-crescent-society"],
        "publisher_candidates": ["PRCS", "Palestinian Red Crescent", "Palestine Red Crescent Society"],
        "coverage_category": "official_humanitarian",
        "reliability_role": "official_humanitarian",
        "risk_if_missing": "Loss of field-response and ambulance/relief signals.",
        "manual_backfill_required": True,
        "core_target": True,
    },
    {
        "target_name": "Gaza Health Ministry",
        "source_id_candidates": ["gaza-health-ministry", "gaza-health-ministry-updates", "ministry-of-health-gaza"],
        "publisher_candidates": ["Gaza Health Ministry", "Ministry of Health in Gaza", "Palestinian Ministry of Health"],
        "coverage_category": "official_claim_source",
        "reliability_role": "official_claim_source",
        "risk_if_missing": "Loss of attribution-sensitive casualty and health claims.",
        "manual_backfill_required": True,
        "core_target": True,
    },
    {
        "target_name": "COGAT / Israeli military coordination source",
        "source_id_candidates": ["cogat", "cogat-updates", "israeli-military-coordination"],
        "publisher_candidates": ["COGAT", "Israeli military coordination source", "IDF", "Israeli military"],
        "coverage_category": "official_claim_source",
        "reliability_role": "official_claim_source",
        "risk_if_missing": "Loss of attribution-sensitive official Israeli claim and coordination context.",
        "manual_backfill_required": True,
        "core_target": True,
    },
    {
        "target_name": "Reuters",
        "source_id_candidates": ["reuters-middle-east-rss"],
        "publisher_candidates": ["Reuters"],
        "coverage_category": "wire_and_major_international",
        "reliability_role": "wire_and_major_international",
        "risk_if_missing": "Loss of high-confidence breaking-news corroboration.",
        "manual_backfill_required": False,
        "core_target": True,
    },
    {
        "target_name": "Associated Press",
        "source_id_candidates": ["ap-middle-east-rss"],
        "publisher_candidates": ["Associated Press", "AP"],
        "coverage_category": "wire_and_major_international",
        "reliability_role": "wire_and_major_international",
        "risk_if_missing": "Loss of high-confidence breaking-news corroboration.",
        "manual_backfill_required": False,
        "core_target": True,
    },
    {
        "target_name": "AFP",
        "source_id_candidates": ["afp-middle-east-rss", "afp-news"],
        "publisher_candidates": ["AFP", "Agence France-Presse"],
        "coverage_category": "wire_and_major_international",
        "reliability_role": "wire_and_major_international",
        "risk_if_missing": "Loss of corroborating wire coverage from a major international outlet.",
        "manual_backfill_required": False,
        "core_target": True,
    },
    {
        "target_name": "BBC",
        "source_id_candidates": ["bbc-middle-east"],
        "publisher_candidates": ["BBC", "BBC News"],
        "coverage_category": "wire_and_major_international",
        "reliability_role": "wire_and_major_international",
        "risk_if_missing": "Loss of major international context and verification.",
        "manual_backfill_required": False,
        "core_target": True,
    },
    {
        "target_name": "The Guardian",
        "source_id_candidates": ["guardian-world"],
        "publisher_candidates": ["The Guardian", "Guardian"],
        "coverage_category": "wire_and_major_international",
        "reliability_role": "wire_and_major_international",
        "risk_if_missing": "Loss of major international context and verification.",
        "manual_backfill_required": False,
        "core_target": True,
    },
    {
        "target_name": "Al Jazeera English",
        "source_id_candidates": ["aljazeera-middle-east", "aljazeera-english"],
        "publisher_candidates": ["Al Jazeera", "Al Jazeera English"],
        "coverage_category": "wire_and_major_international",
        "reliability_role": "wire_and_major_international",
        "risk_if_missing": "Loss of major international context and verification.",
        "manual_backfill_required": False,
        "core_target": True,
    },
    {
        "target_name": "Times of Israel",
        "source_id_candidates": ["times-of-israel-rss"],
        "publisher_candidates": ["Times of Israel"],
        "coverage_category": "region_specialist",
        "reliability_role": "region_specialist",
        "risk_if_missing": "Loss of Israeli-political and local context.",
        "manual_backfill_required": True,
        "core_target": False,
    },
    {
        "target_name": "Haaretz",
        "source_id_candidates": ["haaretz-israel-news"],
        "publisher_candidates": ["Haaretz"],
        "coverage_category": "region_specialist",
        "reliability_role": "region_specialist",
        "risk_if_missing": "Loss of Israeli-political and local context.",
        "manual_backfill_required": True,
        "core_target": False,
    },
    {
        "target_name": "+972 Magazine / Local Call",
        "source_id_candidates": ["972-magazine", "plus972-magazine", "local-call"],
        "publisher_candidates": ["+972 Magazine", "Local Call", "972 Magazine"],
        "coverage_category": "region_specialist",
        "reliability_role": "region_specialist",
        "risk_if_missing": "Loss of regional specialist reporting and analysis.",
        "manual_backfill_required": True,
        "core_target": False,
    },
    {
        "target_name": "Middle East Eye",
        "source_id_candidates": ["middle-east-eye"],
        "publisher_candidates": ["Middle East Eye"],
        "coverage_category": "region_specialist",
        "reliability_role": "region_specialist",
        "risk_if_missing": "Loss of regional specialist reporting and analysis.",
        "manual_backfill_required": True,
        "core_target": False,
    },
    {
        "target_name": "The New Arab",
        "source_id_candidates": ["the-new-arab"],
        "publisher_candidates": ["The New Arab"],
        "coverage_category": "region_specialist",
        "reliability_role": "region_specialist",
        "risk_if_missing": "Loss of regional specialist reporting and analysis.",
        "manual_backfill_required": True,
        "core_target": False,
    },
    {
        "target_name": "WAFA",
        "source_id_candidates": ["wafa-news", "wafa"],
        "publisher_candidates": ["WAFA", "Palestinian News and Information Agency"],
        "coverage_category": "official_claim_source",
        "reliability_role": "official_claim_source",
        "risk_if_missing": "Loss of Palestinian official-claim attribution context.",
        "manual_backfill_required": True,
        "core_target": True,
    },
    {
        "target_name": "UN Human Rights Office",
        "source_id_candidates": ["un-human-rights-office", "ohchr", "ohchr-news"],
        "publisher_candidates": ["UN Human Rights Office", "OHCHR"],
        "coverage_category": "rights_accountability",
        "reliability_role": "rights_accountability",
        "risk_if_missing": "Loss of legal and accountability monitoring.",
        "manual_backfill_required": True,
        "core_target": False,
    },
    {
        "target_name": "Human Rights Watch",
        "source_id_candidates": ["human-rights-watch", "hrw"],
        "publisher_candidates": ["Human Rights Watch", "HRW"],
        "coverage_category": "rights_accountability",
        "reliability_role": "rights_accountability",
        "risk_if_missing": "Loss of legal and accountability monitoring.",
        "manual_backfill_required": True,
        "core_target": False,
    },
    {
        "target_name": "Amnesty International",
        "source_id_candidates": ["amnesty-international", "amnesty"],
        "publisher_candidates": ["Amnesty International", "Amnesty"],
        "coverage_category": "rights_accountability",
        "reliability_role": "rights_accountability",
        "risk_if_missing": "Loss of legal and accountability monitoring.",
        "manual_backfill_required": True,
        "core_target": False,
    },
    {
        "target_name": "Doctors Without Borders / MSF",
        "source_id_candidates": ["msf-news", "doctors-without-borders", "msf"],
        "publisher_candidates": ["Doctors Without Borders", "MSF", "Médecins Sans Frontières"],
        "coverage_category": "rights_accountability",
        "reliability_role": "rights_accountability",
        "risk_if_missing": "Loss of medical-access and field-conditions monitoring.",
        "manual_backfill_required": True,
        "core_target": False,
    },
    {
        "target_name": "Committee to Protect Journalists",
        "source_id_candidates": ["cpj", "committee-to-protect-journalists"],
        "publisher_candidates": ["Committee to Protect Journalists", "CPJ"],
        "coverage_category": "rights_accountability",
        "reliability_role": "rights_accountability",
        "risk_if_missing": "Loss of media-freedom and press-safety monitoring.",
        "manual_backfill_required": True,
        "core_target": False,
    },
    {
        "target_name": "Reporters Without Borders",
        "source_id_candidates": ["rsf", "reporters-without-borders"],
        "publisher_candidates": ["Reporters Without Borders", "RSF"],
        "coverage_category": "rights_accountability",
        "reliability_role": "rights_accountability",
        "risk_if_missing": "Loss of media-freedom and press-safety monitoring.",
        "manual_backfill_required": True,
        "core_target": False,
    },
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    return "_".join(part for part in text.split() if part)


def _collection_report_path(root: Path, edition_date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / "collection_report.json"


def _curation_manifest_candidates(root: Path, edition_date: str) -> list[Path]:
    return [
        root / "data" / "dispatches" / DISPATCH_SLUG / "curated" / edition_date / "curation_manifest.json",
        root / "output" / "site" / DISPATCH_SLUG / "editions" / edition_date / "curation_manifest.json",
        root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / "curation_manifest.json",
    ]


def _report_paths(root: Path) -> tuple[Path, Path]:
    return (
        root / "output" / "review" / DISPATCH_SLUG / "source_coverage_audit.json",
        root / "output" / "review" / DISPATCH_SLUG / "source_coverage_audit.md",
    )


def _infer_source_state(row: dict[str, Any]) -> str:
    source_state = _normalize_text(row.get("source_state"))
    source_tier = _normalize_text(row.get("source_tier"))
    source_id = _normalize_text(row.get("source_id"))
    if source_state:
        return source_state
    if source_id in {"manual_sources_json", "manual_supplement"} or source_tier == "manual_supplements":
        return "manual_only"
    if str(row.get("status") or "").strip().lower() == "skipped":
        return "skipped"
    return "unknown"


def _infer_endpoint_status(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "").strip().lower()
    source_state = _infer_source_state(row)
    reason = _normalize_text(row.get("reason") or row.get("rejection_reason"))
    if status == "ok":
        return "ok"
    if status == "no_matches":
        return "no_matches"
    if source_state == "manual_only":
        if "blocked" in reason or "forbidden" in reason or "403" in reason or "401" in reason:
            return "blocked_endpoint"
        return "manual_only"
    if source_state == "diagnostics_only":
        if "blocked" in reason or "forbidden" in reason or "403" in reason or "401" in reason:
            return "blocked_endpoint"
        return "diagnostics_only"
    if source_state == "disabled":
        if "404" in reason or "dead" in reason:
            return "dead_endpoint"
        return "disabled"
    if status == "failed":
        if "tls" in reason or "certificate" in reason:
            return "blocked_endpoint"
        return "fetch_failed"
    if status == "skipped":
        return "skipped"
    return "unknown"


def _coverage_category_from_row(row: dict[str, Any]) -> str:
    source_tier = _normalize_text(row.get("source_tier"))
    if source_tier in {
        "official_humanitarian",
        "wire_and_major_international",
        "region_specialist",
        "manual_supplements",
        "rights_accountability",
        "official_claim_source",
        "unknown_or_uncategorized",
    }:
        return source_tier
    source_id = _normalize_text(row.get("source_id"))
    if source_id in {"manual_sources_json", "manual_supplement"}:
        return "manual_supplements"
    if source_id in {"who_news", "unrwa_updates", "wfp_newsroom", "unicef_press_releases", "ocha_opt_updates", "prcs_updates"}:
        return "official_humanitarian"
    if source_id in {"gaza_health_ministry", "cogat", "wafa_news"}:
        return "official_claim_source"
    if source_id in {"reuters_middle_east_rss", "ap_middle_east_rss", "afp_middle_east_rss", "bbc_middle_east", "guardian_world", "aljazeera_middle_east"}:
        return "wire_and_major_international"
    if source_id in {"haaretz_israel_news", "times_of_israel_rss", "972_magazine", "plus972_magazine", "local_call", "middle_east_eye", "the_new_arab"}:
        return "region_specialist"
    if source_id in {"un_human_rights_office", "ohchr", "human_rights_watch", "amnesty_international", "msf_news", "doctors_without_borders", "cpj", "rsf"}:
        return "rights_accountability"
    return "unknown_or_uncategorized"


def _risk_if_missing(category: str) -> str:
    return {
        "official_humanitarian": "Loss of official humanitarian coordination and access signals.",
        "wire_and_major_international": "Loss of high-confidence breaking-news corroboration.",
        "region_specialist": "Loss of regional specialist context and counterbalance.",
        "rights_accountability": "Loss of legal and accountability monitoring.",
        "official_claim_source": "Loss of attribution-sensitive official claim and response context.",
        "manual_supplements": "Loss of manual backfill and operator-added context.",
        "unknown_or_uncategorized": "Coverage gap cannot be reasoned about until mapped.",
    }.get(category, "Coverage gap cannot be reasoned about until mapped.")


def _recommended_action(row: dict[str, Any], *, target_missing: bool = False) -> str:
    source_state = _infer_source_state(row)
    status = str(row.get("status") or "").strip().lower()
    endpoint_status = str(row.get("endpoint_status") or "").strip().lower()
    likely_gap = bool(row.get("likely_discovery_gap"))
    if target_missing:
        return "add to registry or document exclusion"
    if row.get("source_id") == "manual_sources_json" or source_state == "manual_only" and _coverage_category_from_row(row) == "manual_supplements":
        return "available for manual source injection"
    if likely_gap:
        return "investigate source discovery gap"
    if source_state == "enabled" and status == "ok":
        return "continue checking"
    if source_state == "enabled" and status == "no_matches":
        return "continue checking; no matching items this run"
    if source_state == "disabled" and endpoint_status == "dead_endpoint":
        return "find replacement endpoint or keep disabled with documented reason"
    if source_state == "diagnostics_only" and endpoint_status == "blocked_endpoint":
        return "keep diagnostics-only or replace with manual/API workflow"
    if source_state == "manual_only":
        return "manual review/backfill required when relevant"
    if source_state == "disabled":
        return "find replacement endpoint or keep disabled with documented reason"
    if source_state == "diagnostics_only":
        return "keep diagnostics-only or replace with manual/API workflow"
    if status == "failed":
        return "investigate source discovery gap"
    if status == "skipped":
        return "document skipped source state and verify it remains intentional"
    return "continue checking"


def _format_status_counts(values: dict[str, int]) -> list[dict[str, Any]]:
    return [{"value": key, "count": count} for key, count in sorted(values.items(), key=lambda item: (-item[1], item[0]))]


def _provider_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row["source_id"] = str(row.get("source_id") or "").strip()
        row["publisher"] = str(row.get("publisher") or "").strip()
        row["url"] = str(row.get("url") or "").strip()
        row["source_state"] = _infer_source_state(row)
        row["status"] = str(row.get("status") or "").strip().lower() or ("skipped" if row["source_state"] != "enabled" else "ok")
        row["reason"] = str(row.get("reason") or row.get("rejection_reason") or row.get("error") or "").strip()
        row["provider_checked"] = bool(row.get("provider_checked", True))
        row["checked_each_run"] = bool(row["source_state"] == "enabled" or row["source_id"] == "manual_sources_json")
        row["raw_candidates"] = int(row.get("raw_candidates") or row.get("raw_items") or 0)
        row["accepted_before_dedupe"] = int(row.get("accepted_before_dedupe") or row.get("accepted") or 0)
        row["kept_after_dedupe"] = int(row.get("kept_after_dedupe") or 0) if row.get("kept_after_dedupe") is not None else None
        row["candidates_seen"] = int(row.get("candidates_seen") or row.get("raw_candidates") or row.get("raw_items") or 0)
        row["candidates_rejected"] = int(row.get("candidates_rejected") or sum(int(v or 0) for v in dict(row.get("rejected_counts") or {}).values()))
        row["rejection_reason"] = str(row.get("rejection_reason") or row["reason"] or "").strip()
        row["likely_discovery_gap"] = bool(row.get("likely_discovery_gap"))
        row["backend_used"] = str(row.get("backend_used") or "python").strip() or "python"
        row["endpoint_status"] = row.get("endpoint_status") or _infer_endpoint_status(row)
        row["manual_backfill_required"] = bool(
            row.get("manual_backfill_required")
            if row.get("manual_backfill_required") is not None
            else row["source_state"] in {"manual_only", "diagnostics_only"}
        )
        row["coverage_category"] = _coverage_category_from_row(row)
        row["reliability_role"] = str(row.get("reliability_role") or row["coverage_category"]).strip() or row["coverage_category"]
        row["risk_if_missing"] = str(row.get("risk_if_missing") or _risk_if_missing(row["coverage_category"])).strip()
        row["recommended_action"] = _recommended_action(row)
        rows.append(row)
    return rows


def _match_target_row(provider_rows: list[dict[str, Any]], target: dict[str, Any]) -> dict[str, Any] | None:
    target_name = _normalize_text(target["target_name"])
    target_sources = {_normalize_text(item) for item in target["source_id_candidates"]}
    target_publishers = {_normalize_text(item) for item in target["publisher_candidates"]}
    for row in provider_rows:
        source_id = _normalize_text(row.get("source_id"))
        publisher = _normalize_text(row.get("publisher"))
        if source_id in target_sources:
            return row
        if publisher in target_publishers or any(candidate and candidate in publisher for candidate in target_publishers) or any(publisher and publisher in candidate for candidate in target_publishers):
            return row
        if target_name and target_name in publisher:
            return row
    return None


def _target_rows(provider_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in TARGET_SOURCE_UNIVERSE:
        match = _match_target_row(provider_rows, target)
        if match is None:
            category = str(target["coverage_category"])
            row = {
                "source_id": str(target["source_id_candidates"][0]),
                "target_name": str(target["target_name"]),
                "publisher": str(target["target_name"]),
                "source_tier": category,
                "source_state": "missing_from_registry",
                "status": "missing_from_registry",
                "reason": "not present in collection report registry",
                "url": "",
                "provider_checked": False,
                "checked_each_run": False,
                "raw_candidates": 0,
                "accepted_before_dedupe": 0,
                "kept_after_dedupe": 0,
                "candidates_seen": 0,
                "candidates_rejected": 0,
                "rejection_reason": "not present in collection report registry",
                "likely_discovery_gap": False,
                "backend_used": "",
                "endpoint_status": "missing_from_registry",
                "manual_backfill_required": bool(target.get("manual_backfill_required", False)),
                "coverage_category": category,
                "reliability_role": str(target.get("reliability_role") or category),
                "risk_if_missing": str(target.get("risk_if_missing") or _risk_if_missing(category)),
                "recommended_action": _recommended_action({}, target_missing=True),
            }
            rows.append(row)
            continue

        source_state = _infer_source_state(match)
        status = {
            "enabled": "present_enabled",
            "manual_only": "present_manual_only",
            "diagnostics_only": "present_diagnostics_only",
            "disabled": "present_disabled",
        }.get(source_state, "present_skipped")
        category = str(target.get("coverage_category") or _coverage_category_from_row(match))
        target_row = {
            "source_id": str(target["source_id_candidates"][0]),
            "matched_source_id": str(match.get("source_id") or ""),
            "target_name": str(target["target_name"]),
            "publisher": str(match.get("publisher") or target["target_name"]),
            "source_tier": str(match.get("source_tier") or category),
            "source_state": source_state,
            "status": status,
            "reason": str(match.get("reason") or match.get("rejection_reason") or ""),
            "url": str(match.get("url") or ""),
            "provider_checked": bool(match.get("provider_checked", True)),
            "checked_each_run": bool(source_state == "enabled"),
            "raw_candidates": int(match.get("raw_candidates") or match.get("raw_items") or 0),
            "accepted_before_dedupe": int(match.get("accepted_before_dedupe") or match.get("accepted") or 0),
            "kept_after_dedupe": int(match.get("kept_after_dedupe") or 0) if match.get("kept_after_dedupe") is not None else None,
            "candidates_seen": int(match.get("candidates_seen") or match.get("raw_candidates") or match.get("raw_items") or 0),
            "candidates_rejected": int(match.get("candidates_rejected") or sum(int(v or 0) for v in dict(match.get("rejected_counts") or {}).values())),
            "rejection_reason": str(match.get("rejection_reason") or match.get("reason") or ""),
            "likely_discovery_gap": bool(match.get("likely_discovery_gap")),
            "backend_used": str(match.get("backend_used") or "python"),
            "endpoint_status": str(match.get("endpoint_status") or _infer_endpoint_status(match)),
            "manual_backfill_required": bool(
                match.get("manual_backfill_required")
                if match.get("manual_backfill_required") is not None
                else source_state in {"manual_only", "diagnostics_only"}
            ),
            "coverage_category": category,
            "reliability_role": str(target.get("reliability_role") or category),
            "risk_if_missing": str(target.get("risk_if_missing") or _risk_if_missing(category)),
            "recommended_action": _recommended_action(match),
        }
        rows.append(target_row)
    return rows


def _summary_counts(provider_rows: list[dict[str, Any]], target_rows: list[dict[str, Any]]) -> dict[str, Any]:
    source_state_counts = Counter(str(row.get("source_state") or "unknown") for row in provider_rows)
    provider_status_counts = Counter(str(row.get("status") or "unknown") for row in provider_rows)
    endpoint_status_counts = Counter(str(row.get("endpoint_status") or "unknown") for row in provider_rows)
    coverage_category_counts = Counter(str(row.get("coverage_category") or "unknown_or_uncategorized") for row in provider_rows)
    target_status_counts = Counter(str(row.get("status") or "unknown") for row in target_rows)
    return {
        "provider_count": len(provider_rows),
        "target_count": len(target_rows),
        "source_state": dict(sorted(source_state_counts.items())),
        "provider_status": dict(sorted(provider_status_counts.items())),
        "endpoint_status": dict(sorted(endpoint_status_counts.items())),
        "coverage_category": dict(sorted(coverage_category_counts.items())),
        "target_coverage_status": dict(sorted(target_status_counts.items())),
    }


def _action_rows(provider_rows: list[dict[str, Any]], target_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in provider_rows + target_rows:
        action = str(row.get("recommended_action") or "").strip()
        if not action:
            continue
        rows.append(
            {
                "source_id": row.get("source_id"),
                "target_name": row.get("target_name"),
                "publisher": row.get("publisher"),
                "status": row.get("status"),
                "source_state": row.get("source_state"),
                "endpoint_status": row.get("endpoint_status"),
                "recommended_action": action,
                "reason": row.get("reason") or row.get("rejection_reason") or "",
            }
        )
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("source_id") or row.get("target_name") or ""),
            str(row.get("recommended_action") or ""),
            str(row.get("status") or ""),
        )
        unique[key] = row
    return sorted(unique.values(), key=lambda row: (str(row.get("recommended_action") or ""), str(row.get("source_id") or row.get("target_name") or "")))


def _load_first_existing_json(paths: list[Path]) -> tuple[Path | None, Any]:
    for path in paths:
        if path.exists():
            return path, _read_json(path)
    return None, None


def _rendered_public_story_rows(root: Path, edition_date: str) -> tuple[Path | None, list[dict[str, Any]], list[str]]:
    path, payload = _load_first_existing_json(_curation_manifest_candidates(root, edition_date))
    if not isinstance(payload, list):
        return path, [], []
    rows = [row for row in payload if isinstance(row, dict) and bool(row.get("public_rendered", row.get("included_in_public_summary", True)))]
    source_ids: list[str] = []
    for row in rows:
        for source_id in row.get("source_ids") or row.get("source_record_ids") or []:
            text = str(source_id or "").strip()
            if text:
                source_ids.append(text)
    return path, rows, sorted(dict.fromkeys(source_ids))


def _public_source_rows(rendered_public_story_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    rows: dict[str, dict[str, Any]] = {}
    for story in rendered_public_story_rows:
        source_ids = story.get("source_ids") or story.get("source_record_ids") or []
        source_records = list(story.get("source_records") or [])
        publisher_names = list(story.get("publisher_names") or [])
        source_urls = list(story.get("source_urls") or [])
        for index, source_id_value in enumerate(source_ids):
            source_id = str(source_id_value or "").strip()
            if not source_id:
                continue
            counts[source_id] = counts.get(source_id, 0) + 1
            if source_id in rows:
                continue
            source_record = source_records[index] if index < len(source_records) and isinstance(source_records[index], dict) else {}
            rows[source_id] = {
                "source_id": source_id,
                "title": str(source_record.get("title") or story.get("title") or ""),
                "publisher": str(
                    source_record.get("publisher")
                    or (publisher_names[index] if index < len(publisher_names) else "")
                    or (publisher_names[0] if publisher_names else "")
                    or story.get("publisher")
                    or ""
                ),
                "url": str(source_record.get("url") or (source_urls[index] if index < len(source_urls) else "") or (source_urls[0] if source_urls else "") or story.get("url") or ""),
                "reliability_tier": str(source_record.get("reliability_tier") or story.get("reliability_tier") or ""),
                "attribution_mode": str(source_record.get("attribution_mode") or story.get("attribution_mode") or ""),
                "public_story_count": 0,
            }
    public_sources = list(rows.values())
    for row in public_sources:
        row["public_story_count"] = counts.get(str(row.get("source_id") or ""), 0)
    public_sources.sort(key=lambda row: (str(row.get("publisher") or "").lower(), str(row.get("source_id") or "")))
    return public_sources


def _warnings(target_rows: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    core_problems = [
        row
        for row in target_rows
        if any(target["target_name"] == row.get("target_name") for target in TARGET_SOURCE_UNIVERSE if target.get("core_target"))
        and str(row.get("status") or "") != "present_enabled"
    ]
    if core_problems:
        warnings.append(
            "Core Gaza target sources are missing or not actively checked: "
            + ", ".join(f"{row['target_name']} ({row['status']})" for row in core_problems)
        )
    missing = [row for row in target_rows if row.get("status") == "missing_from_registry"]
    if missing:
        warnings.append(f"{len(missing)} target reliable sources are missing from the registry.")
    return warnings


def build_audit(root: Path, edition_date: str) -> dict[str, Any]:
    collection_report_path = _collection_report_path(root, edition_date)
    if not collection_report_path.exists():
        raise FileNotFoundError(f"collection report not found: {collection_report_path}")
    collection_report = _read_json(collection_report_path)
    if not isinstance(collection_report, dict):
        raise ValueError(f"collection report must be a JSON object: {collection_report_path}")

    attempted_rows = collection_report.get("source_providers_attempted") or collection_report.get("provider_diagnostics") or []
    provider_rows = _provider_rows([row for row in attempted_rows if isinstance(row, dict)])
    target_rows = _target_rows(provider_rows)
    rendered_manifest_path, rendered_public_story_rows, rendered_public_story_source_ids = _rendered_public_story_rows(root, edition_date)
    rendered_public_story_sources = _public_source_rows(rendered_public_story_rows)
    warnings = _warnings(target_rows)
    if rendered_public_story_rows and not rendered_public_story_source_ids:
        warnings.append("Rendered public stories were found, but no source IDs could be resolved from the curation manifest.")
    summary_counts = _summary_counts(provider_rows, target_rows)
    recommended_actions = _action_rows(provider_rows, target_rows)
    missing_target_sources = [row for row in target_rows if row.get("status") == "missing_from_registry"]
    manual_backfill_sources = [
        row
        for row in target_rows
        if bool(row.get("manual_backfill_required"))
        and str(row.get("status") or "") in {"present_manual_only", "present_diagnostics_only", "missing_from_registry", "present_disabled"}
    ]
    blocked_or_disabled_sources = [
        row
        for row in target_rows
        if str(row.get("status") or "") in {"present_manual_only", "present_diagnostics_only", "present_disabled", "present_skipped"}
        or str(row.get("endpoint_status") or "") in {"blocked_endpoint", "dead_endpoint", "manual_only"}
    ]
    report = {
        "ok": True,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "collection_report_path": str(collection_report_path),
        "curation_manifest_path": str(rendered_manifest_path) if rendered_manifest_path else None,
        "generated_at": _utc_now(),
        "summary_counts": summary_counts,
        "providers": provider_rows,
        "target_source_coverage": target_rows,
        "rendered_public_story_count": len(rendered_public_story_rows),
        "rendered_public_stories": rendered_public_story_rows,
        "rendered_public_story_source_ids": rendered_public_story_source_ids,
        "rendered_public_story_sources": rendered_public_story_sources,
        "recommended_actions": recommended_actions,
        "missing_target_sources": missing_target_sources,
        "manual_backfill_sources": manual_backfill_sources,
        "blocked_or_disabled_sources": blocked_or_disabled_sources,
        "warnings": warnings,
    }
    json_path, md_path = _report_paths(root)
    report["json_report_path"] = str(json_path)
    report["markdown_report_path"] = str(md_path)
    return report


def _render_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "_None._"
    header = "| " + " | ".join(label for _key, label in columns) + " |"
    separator = "| " + " | ".join("---" for _key, _label in columns) + " |"
    lines = [header, separator]
    for row in rows:
        values = []
        for key, _label in columns:
            value = row.get(key)
            if isinstance(value, bool):
                text = "yes" if value else "no"
            elif value is None:
                text = ""
            else:
                text = str(value)
            text = text.replace("|", "\\|")
            values.append(text)
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    providers = list(report.get("providers") or [])
    targets = list(report.get("target_source_coverage") or [])
    enabled = [row for row in providers if str(row.get("source_state") or "") == "enabled"]
    important_not_checked = [
        row
        for row in targets
        if str(row.get("status") or "") in {"present_manual_only", "present_diagnostics_only", "present_disabled", "missing_from_registry"}
    ]
    manual_only = [row for row in targets if str(row.get("status") or "") == "present_manual_only" or str(row.get("source_state") or "") == "manual_only"]
    blocked_disabled = [row for row in targets if str(row.get("status") or "") in {"present_diagnostics_only", "present_disabled"}]
    missing = list(report.get("missing_target_sources") or [])
    rendered_story_sources = list(report.get("rendered_public_story_sources") or [])
    recommended_actions = list(report.get("recommended_actions") or [])
    warnings = list(report.get("warnings") or [])

    lines = [
        f"# Gaza Source Coverage Audit",
        "",
        f"- Edition date: `{report.get('edition_date')}`",
        f"- Collection report: `{report.get('collection_report_path')}`",
        f"- Generated at: `{report.get('generated_at')}`",
    ]
    if warnings:
        lines.extend(["", "## Warnings"])
        lines.extend([f"- {warning}" for warning in warnings])

    lines.extend(
        [
            "",
            "## Summary Counts",
            "",
            "### Provider Source State Counts",
            _render_table(_format_status_counts(report.get("summary_counts", {}).get("source_state", {})), [("value", "source_state"), ("count", "count")]),
            "",
            "### Provider Status Counts",
            _render_table(_format_status_counts(report.get("summary_counts", {}).get("provider_status", {})), [("value", "status"), ("count", "count")]),
            "",
            "### Target Coverage Counts",
            _render_table(_format_status_counts(report.get("summary_counts", {}).get("target_coverage_status", {})), [("value", "status"), ("count", "count")]),
            "",
            "## Enabled Sources Checked This Run",
            _render_table(
                enabled,
                [
                    ("source_id", "source_id"),
                    ("publisher", "publisher"),
                    ("status", "status"),
                    ("raw_candidates", "raw_candidates"),
                    ("accepted_before_dedupe", "accepted_before_dedupe"),
                    ("kept_after_dedupe", "kept_after_dedupe"),
                    ("endpoint_status", "endpoint_status"),
                    ("recommended_action", "recommended_action"),
                ],
            ),
            "",
            "## Important Reliable Sources Not Actively Checked",
            _render_table(
                important_not_checked,
                [
                    ("target_name", "target_source"),
                    ("status", "status"),
                    ("source_state", "source_state"),
                    ("coverage_category", "coverage_category"),
                    ("reason", "reason"),
                    ("recommended_action", "recommended_action"),
                ],
            ),
            "",
            "## Manual-Only Sources Requiring Review/Backfill",
            _render_table(
                manual_only,
                [
                    ("target_name", "target_source"),
                    ("status", "status"),
                    ("source_state", "source_state"),
                    ("manual_backfill_required", "manual_backfill_required"),
                    ("risk_if_missing", "risk_if_missing"),
                    ("recommended_action", "recommended_action"),
                ],
            ),
            "",
            "## Disabled or Blocked Sources With Reasons",
            _render_table(
                blocked_disabled,
                [
                    ("target_name", "target_source"),
                    ("status", "status"),
                    ("source_state", "source_state"),
                    ("endpoint_status", "endpoint_status"),
                    ("reason", "reason"),
                    ("recommended_action", "recommended_action"),
                ],
            ),
            "",
            "## Missing Target Reliable Sources",
            _render_table(
                missing,
                [
                    ("target_name", "target_source"),
                    ("status", "status"),
                    ("coverage_category", "coverage_category"),
                    ("risk_if_missing", "risk_if_missing"),
                    ("recommended_action", "recommended_action"),
                ],
            ),
            "",
            "## Recommended Next Actions",
        ]
    )
    if recommended_actions:
        for row in recommended_actions:
            subject = str(row.get("target_name") or row.get("source_id") or "unknown")
            lines.append(f"- `{subject}`: {row.get('recommended_action')} ({row.get('status')})")
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Sources Contributing Rendered Public Stories",
            _render_table(
                rendered_story_sources,
                [
                    ("source_id", "source_id"),
                    ("publisher", "publisher"),
                    ("public_story_count", "public_story_count"),
                    ("reliability_tier", "reliability_tier"),
                    ("attribution_mode", "attribution_mode"),
                    ("url", "url"),
                ],
            ),
            "",
            "## Provider Rows",
            _render_table(
                providers,
                [
                    ("source_id", "source_id"),
                    ("publisher", "publisher"),
                    ("source_state", "source_state"),
                    ("status", "status"),
                    ("reason", "reason"),
                    ("coverage_category", "coverage_category"),
                    ("endpoint_status", "endpoint_status"),
                ],
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def write_audit_report(root: Path, edition_date: str) -> dict[str, Any]:
    report = build_audit(root, edition_date)
    json_path, md_path = _report_paths(root)
    _write_json(json_path, report)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Gaza reliable-source coverage against the configured target universe.")
    parser.add_argument("--date", required=True, help="Edition date in YYYY-MM-DD format.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = write_audit_report(ROOT, args.date)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
