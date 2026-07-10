from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bluefern_dispatches.food_line_sources import canonical_url, normalize_title, validate_date

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ACTIVE_BENCHMARK_STATUSES = {"approved", "reviewed"}
TRACKING_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}
OUTLET_SUFFIX_HINTS = {
    "news",
    "abc",
    "cbs",
    "nbc",
    "fox",
    "pbs",
    "post",
    "times",
    "tribune",
    "herald",
    "journal",
    "press",
    "daily",
    "magazine",
    "flyer",
    "republic",
    "sun",
    "union",
    "newsnow",
    "ktal",
    "ksbw",
    "wdrb",
    "kiiitv",
    "wmar",
    "kmtv",
    "koaa",
    "aol",
}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append(dict(row))
    except OSError:
        return []
    return rows


def _nonempty(value: Any) -> str:
    return str(value or "").strip()


def _normalize_url_for_match(url: str) -> str:
    value = _nonempty(url)
    if not value:
        return ""
    parsed = urlsplit(value)
    query_items = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_PARAMS and not key.lower().startswith("utm_")
    ]
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            urlencode(query_items, doseq=True),
            "",
        )
    )


def _title_core_text(text: str) -> str:
    normalized = normalize_title(text)
    if " - " not in normalized:
        return normalized
    head, tail = normalized.rsplit(" - ", 1)
    tail_terms = re.findall(r"[a-z0-9]+", tail.lower())
    if tail_terms and len(tail_terms) <= 5 and any(hint in tail.lower() for hint in OUTLET_SUFFIX_HINTS):
        return head
    return normalized


def _title_terms(text: str) -> list[str]:
    stopwords = {
        "about",
        "after",
        "again",
        "amid",
        "before",
        "during",
        "from",
        "into",
        "local",
        "news",
        "more",
        "new",
        "of",
        "report",
        "reports",
        "said",
        "say",
        "says",
        "than",
        "that",
        "the",
        "this",
        "those",
        "through",
        "today",
        "under",
        "while",
        "with",
        "without",
        "year",
        "yesterday",
    }
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9]+", _title_core_text(text).lower()):
        if len(token) < 4 or token in stopwords:
            continue
        if token not in terms:
            terms.append(token)
    return terms


def _url_domain(url: str) -> str:
    try:
        return urlsplit(_normalize_url_for_match(url)).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""


def _record_value(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _nonempty(record.get(key))
        if value:
            return value
    return ""


def _record_date(record: dict[str, Any]) -> str:
    for key in ("published_at", "publication_date", "page_metadata_date", "source_published_date", "date"):
        value = _record_value(record, key)
        if value:
            return value[:10]
    return ""


def _record_location(record: dict[str, Any]) -> str:
    return _record_value(record, "location", "location_name", "city", "county_name", "state")


def _record_source_family(record: dict[str, Any]) -> str:
    return _record_value(record, "source_family", "family")


def _record_discovery_channel(record: dict[str, Any]) -> str:
    for key in ("discovery_channel", "source_type", "collector_source_type"):
        value = _record_value(record, key)
        if value:
            return value
    if _record_value(record, "google_news_url"):
        return "google_news"
    return ""


def _record_title(record: dict[str, Any]) -> str:
    return _record_value(record, "title", "discovered_title", "source_title", "matched_title", "headline")


def _record_url(record: dict[str, Any]) -> str:
    return _record_value(
        record,
        "url",
        "final_trace_url",
        "canonical_url",
        "discovered_url",
        "source_url",
        "primary_source_url",
        "resolved_url",
        "candidate_url",
        "google_news_url",
    )


def _record_match_key(record: dict[str, Any]) -> str:
    url = _normalize_url_for_match(_record_url(record))
    if url:
        return f"url:{url}"
    title = normalize_title(_record_title(record))
    publisher = normalize_title(_record_value(record, "publisher", "source_name", "source_publisher"))
    date = _record_date(record)
    if title or publisher or date:
        return f"text:{title}|{publisher}|{date}"
    identifier = _record_value(record, "source_record_id", "candidate_id", "source_id")
    return f"id:{identifier}" if identifier else ""


def _boolish(record: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = record.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    return None


def _record_exclusion_reason(record: dict[str, Any]) -> str:
    for key in (
        "exclusion_reason",
        "reason",
        "freshness_disqualification_reason",
        "source_freshness_disqualification_reason",
        "primary_disqualification_reason",
        "pressure_reason",
        "fetch_error",
        "fetch_failure_reason",
        "miss_reason",
    ):
        value = _record_value(record, key)
        if value:
            return value

    classification_status = _record_value(record, "classification_status")
    if classification_status in {"context_only", "duplicate", "duplicate_or_known"}:
        return "resource-only / no pressure signal" if classification_status == "context_only" else "duplicate"

    source_role = _record_value(record, "source_role")
    source_purpose = _record_value(record, "source_purpose")
    pressure_verification_status = _record_value(record, "pressure_verification_status")
    source_freshness_status = _record_value(record, "source_freshness_status", "freshness_status")
    if source_freshness_status.startswith("stale") or "outside daily window" in source_freshness_status:
        return "stale"
    if source_role in {"resource_context", "baseline_condition"} or source_purpose in {"resource_page", "donation_page", "evergreen_context", "program_description"}:
        return "resource-only / no pressure signal"
    if pressure_verification_status == "demoted_context":
        return "weak pressure signal"
    if _record_value(record, "published_at") == "":
        return "missing usable date"
    if not _record_url(record):
        return "insufficient source traceability"
    return ""


def _record_status(record: dict[str, Any]) -> str:
    duplicate_of = _record_value(record, "duplicate_of")
    if duplicate_of or _record_value(record, "duplicate") in {"true", "1"}:
        return "duplicate"

    included = _boolish(record, "included")
    if included is None:
        included = _boolish(
            record,
            "source_public_story_eligible",
            "qualifies_for_public_inclusion",
            "primary_eligible",
        )
    if included is True:
        return "included"

    explicit_reason = _record_exclusion_reason(record)
    if explicit_reason:
        return "excluded"

    if any(
        _record_value(record, key)
        for key in (
            "classification_status",
            "review_status",
            "pressure_signal",
            "pressure_type",
            "pressure_verification_status",
            "source_role",
            "source_type",
            "source_family",
        )
    ):
        return "unresolved"
    return "unresolved"


def _record_classification_reason(record: dict[str, Any]) -> str:
    if _record_status(record) != "excluded":
        return ""
    return _record_exclusion_reason(record)


def _canonical_title_match_score(candidate_title: str, record_title: str) -> float:
    candidate_core = normalize_title(_title_core_text(candidate_title))
    record_core = normalize_title(_title_core_text(record_title))
    if not candidate_core or not record_core:
        return 0.0
    return SequenceMatcher(None, candidate_core, record_core).ratio()


def _record_matches_benchmark(benchmark: dict[str, Any], record: dict[str, Any]) -> tuple[bool, str, float]:
    benchmark_url = _normalize_url_for_match(_record_value(benchmark, "url"))
    record_url = _normalize_url_for_match(_record_url(record))
    if benchmark_url and record_url and benchmark_url == record_url:
        return True, "canonical_url", 1.0

    benchmark_title = _record_value(benchmark, "title")
    record_title = _record_title(record)
    if not benchmark_title or not record_title:
        return False, "", 0.0

    score = _canonical_title_match_score(benchmark_title, record_title)
    if score < 0.88:
        return False, "", score

    benchmark_domain = _url_domain(_record_value(benchmark, "url"))
    record_domain = _url_domain(_record_url(record))
    benchmark_publisher = normalize_title(_record_value(benchmark, "publisher"))
    record_publisher = normalize_title(_record_value(record, "publisher", "source_name"))

    if benchmark_domain and record_domain and benchmark_domain == record_domain:
        return True, "title_similarity", score
    if benchmark_publisher and record_publisher and benchmark_publisher == record_publisher:
        return True, "publisher_title_similarity", score
    if not benchmark_domain and not benchmark_publisher:
        return True, "title_similarity", score
    return False, "", score


def _load_source_records_for_date(root: Path, date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sources_dir = root / "data" / "dispatches" / "food-line" / "sources" / date
    if sources_dir.exists():
        for path in sorted(sources_dir.glob("*.json")):
            payload = _read_json(path)
            if not isinstance(payload, list):
                continue
            for row in payload:
                if isinstance(row, dict):
                    rows.append({**row, "_artifact_kind": "source_records", "_artifact_path": str(path)})

    review_path = root / "output" / "review" / "food-line" / date / "pressure_review.csv"
    if review_path.exists():
        for row in _read_csv_rows(review_path):
            rows.append({**row, "_artifact_kind": "pressure_review", "_artifact_path": str(review_path)})

    discovery_candidates_path = root / "data" / "dispatches" / "food-line" / "discovery" / date / "discovery_candidates.json"
    if discovery_candidates_path.exists():
        payload = _read_json(discovery_candidates_path)
        if isinstance(payload, list):
            for row in payload:
                if isinstance(row, dict):
                    rows.append({**row, "_artifact_kind": "discovery_candidates", "_artifact_path": str(discovery_candidates_path)})

    discovery_intake_path = root / "output" / "review" / "food-line" / date / "discovery_intake.json"
    if discovery_intake_path.exists():
        payload = _read_json(discovery_intake_path)
        if isinstance(payload, dict):
            for row in payload.get("discovery_source_rows") or []:
                if isinstance(row, dict):
                    rows.append({**row, "_artifact_kind": "discovery_intake", "_artifact_path": str(discovery_intake_path)})

    discovery_gap_path = root / "data" / "dispatches" / "food-line" / "discovery_gap" / date / "discovery_gap_report.json"
    if discovery_gap_path.exists():
        payload = _read_json(discovery_gap_path)
        if isinstance(payload, dict):
            for row in payload.get("candidates") or []:
                if isinstance(row, dict):
                    rows.append({**row, "_artifact_kind": "discovery_gap", "_artifact_path": str(discovery_gap_path)})

    collector_audit_path = root / "data" / "dispatches" / "food-line" / "sources" / date / "collector_audit.json"
    if collector_audit_path.exists():
        payload = _read_json(collector_audit_path)
        if isinstance(payload, list):
            for row in payload:
                if isinstance(row, dict):
                    rows.append({**row, "_artifact_kind": "collector_audit", "_artifact_path": str(collector_audit_path)})

    return rows


def _load_run_manifest(root: Path, date: str) -> dict[str, Any] | None:
    path = root / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json"
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else None


def _load_source_performance_history(root: Path) -> dict[str, Any]:
    path = root / "data" / "dispatches" / "food-line" / "source_performance_history.json"
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else {}


def _load_query_performance_rows(root: Path) -> list[dict[str, Any]]:
    path = root / "data" / "dispatches" / "food-line" / "source_discovery_query_performance.json"
    payload = _read_json(path)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _load_discovery_queries(root: Path) -> dict[str, Any]:
    path = root / "data" / "dispatches" / "food-line" / "discovery_gap_queries.json"
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else {"queries": [], "exclude_domains": []}


def _load_benchmarks(root: Path, start_date: str, end_date: str, benchmark_file: Path | None) -> list[dict[str, Any]]:
    candidate_path = benchmark_file
    if candidate_path is None:
        candidate_path = root / "data" / "dispatches" / "food-line" / "coverage_benchmarks" / f"{start_date}_{end_date}.json"
    if not candidate_path.exists():
        return []
    payload = _read_json(candidate_path)
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in payload:
        if isinstance(row, dict):
            rows.append({**row, "_benchmark_path": str(candidate_path)})
    return rows


def _benchmark_match_key(record: dict[str, Any]) -> str:
    url = _normalize_url_for_match(_record_value(record, "url"))
    if url:
        return f"url:{url}"
    title = normalize_title(_title_core_text(_record_value(record, "title")))
    publisher = normalize_title(_record_value(record, "publisher"))
    published_at = _record_value(record, "published_at")[:10]
    if title or publisher or published_at:
        return f"text:{title}|{publisher}|{published_at}"
    return ""


def _complete_date_range(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(validate_date(start_date), "%Y-%m-%d").date()
    end = datetime.strptime(validate_date(end_date), "%Y-%m-%d").date()
    if end < start:
        raise ValueError("end-date must be on or after start-date")
    dates = []
    current = start
    while current <= end:
        dates.append(current.isoformat())
        current += timedelta(days=1)
    return dates


def _artifact_availability(root: Path, date: str) -> dict[str, Any]:
    source_dir = root / "data" / "dispatches" / "food-line" / "sources" / date
    review_dir = root / "output" / "review" / "food-line" / date
    discovery_dir = root / "data" / "dispatches" / "food-line" / "discovery" / date
    gap_dir = root / "data" / "dispatches" / "food-line" / "discovery_gap" / date
    edition_dir = root / "data" / "dispatches" / "food-line" / "editions" / date
    source_files = {path.name: path.exists() for path in sorted(source_dir.glob("*.json"))} if source_dir.exists() else {}
    review_files = {
        "pressure_review.csv": (review_dir / "pressure_review.csv").exists(),
        "discovery_intake.json": (review_dir / "discovery_intake.json").exists(),
    }
    artifact_families = {
        "run_manifest": (edition_dir / "run_manifest.json").exists(),
        "collector_audit": source_files.get("collector_audit.json", False),
        "source_records": any(source_files.get(name, False) for name in ("auto_sources.json", "manual_sources.json", "discovery_sources.json")),
        "pressure_review": review_files["pressure_review.csv"],
        "discovery_candidates": (discovery_dir / "discovery_candidates.json").exists(),
        "discovery_intake": review_files["discovery_intake.json"],
        "discovery_gap_report": (gap_dir / "discovery_gap_report.json").exists(),
        "source_performance_history": (root / "data" / "dispatches" / "food-line" / "source_performance_history.json").exists(),
        "source_registry": (root / "data" / "dispatches" / "food-line" / "source_registry.json").exists(),
        "discovery_queries": (root / "data" / "dispatches" / "food-line" / "discovery_gap_queries.json").exists(),
    }
    present_count = sum(1 for value in artifact_families.values() if value)
    total_count = len(artifact_families)
    return {
        "source_dir_exists": source_dir.exists(),
        "source_files": source_files,
        "review_files": review_files,
        "artifact_families": artifact_families,
        "completeness_ratio": round(present_count / total_count if total_count else 0.0, 3),
        "available_count": present_count,
        "expected_count": total_count,
    }


def _benchmark_active(record: dict[str, Any]) -> bool:
    status = _record_value(record, "review_status").lower()
    return status in ACTIVE_BENCHMARK_STATUSES


def _summarize_rows(rows: list[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    examples: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = key_fn(row)
        if not key:
            continue
        counts[key] += 1
        examples.setdefault(key, row)
    summary: list[dict[str, Any]] = []
    for key, count in counts.most_common():
        example = examples[key]
        summary.append(
            {
                "key": key,
                "count": count,
                "publisher": _record_value(example, "publisher", "source_name"),
                "location": _record_location(example),
                "source_family": _record_source_family(example),
                "pressure_type": _record_value(example, "pressure_type"),
                "query_text": _record_value(example, "query_text", "query"),
                "discovery_channel": _record_discovery_channel(example),
            }
        )
    return summary


def _record_text_values(record: dict[str, Any], *keys: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for key in keys:
        raw = record.get(key)
        if isinstance(raw, (list, tuple)):
            items = raw
        else:
            items = [raw]
        for item in items:
            text = _nonempty(item)
            if not text or text in seen:
                continue
            values.append(text)
            seen.add(text)
    return values


def _merge_text_lists(*values: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in values:
        items = value if isinstance(value, (list, tuple)) else [value]
        for item in items:
            text = _nonempty(item)
            if not text or text in seen:
                continue
            merged.append(text)
            seen.add(text)
    return merged


def _record_query_attribution(record: dict[str, Any]) -> dict[str, Any]:
    query_ids = _record_text_values(record, "discovery_query_ids", "discovery_query_id", "query_template", "query")
    query_texts = _record_text_values(record, "discovery_queries", "discovery_query_texts", "discovery_query_text", "query_text", "discovery_query")
    query_groups = _record_text_values(record, "discovery_query_groups", "discovery_query_group", "query_family", "category")
    discovery_channels = _record_text_values(record, "discovery_channels", "discovery_channel", "source_type", "collector_source_type")
    discovery_providers = _record_text_values(record, "discovery_providers", "discovery_provider", "search_provider")
    original_discovery_urls = _record_text_values(
        record,
        "original_discovery_urls",
        "original_discovery_url",
        "discovery_seed_url",
        "source_seed_url",
        "query_url",
        "google_news_url",
    )
    discovered_at = _record_value(record, "discovered_at", "first_discovered_at", "last_discovered_at", "retrieved_at", "published_at", "page_metadata_date")
    resolved_source_url = _record_url(record)
    collector_run_id = _record_value(record, "collector_run_id", "run_id", "edition_date")
    attribution_present = bool(query_ids or query_texts or query_groups or discovery_channels or discovery_providers or original_discovery_urls or discovered_at)
    return {
        "query_ids": query_ids,
        "query_texts": query_texts,
        "query_groups": query_groups,
        "discovery_channels": discovery_channels,
        "discovery_providers": discovery_providers,
        "original_discovery_urls": original_discovery_urls,
        "resolved_source_url": resolved_source_url,
        "discovered_at": discovered_at,
        "collector_run_id": collector_run_id,
        "attribution_present": attribution_present,
        "attribution_state": "recorded" if attribution_present else "not_recorded",
    }


def _query_summary_key(attribution: dict[str, Any]) -> str:
    for key in ("query_ids", "query_texts", "query_groups", "discovery_providers", "discovery_channels"):
        values = attribution.get(key) or []
        if values:
            return str(values[0]).strip()
    return "unknown"


def _query_summary_label(attribution: dict[str, Any]) -> tuple[str, str, str]:
    query_id = ""
    query_text = ""
    query_category = ""
    query_ids = attribution.get("query_ids") or []
    query_texts = attribution.get("query_texts") or []
    query_groups = attribution.get("query_groups") or []
    if query_ids:
        query_id = str(query_ids[0]).strip()
    elif query_texts:
        query_id = str(query_texts[0]).strip()
    elif query_groups:
        query_id = str(query_groups[0]).strip()
    if query_texts:
        query_text = str(query_texts[0]).strip()
    elif query_ids:
        query_text = str(query_ids[0]).strip()
    elif query_groups:
        query_text = str(query_groups[0]).strip()
    if query_groups:
        query_category = str(query_groups[0]).strip()
    return query_id or "unknown", query_text or "unknown", query_category or "unknown"


def _query_analysis_rows(records: list[dict[str, Any]], *, configured_queries: list[str], performance_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    query_buckets: dict[str, dict[str, Any]] = {}
    unattributed_records = 0
    performance_lookup: dict[str, dict[str, Any]] = {}
    for row in performance_rows or []:
        if not isinstance(row, dict):
            continue
        key = _nonempty(row.get("query_id") or row.get("query_template") or row.get("query_text") or row.get("template"))
        if key and key not in performance_lookup:
            performance_lookup[key] = row
    for record in records:
        attribution = _record_query_attribution(record)
        if not attribution["attribution_present"]:
            unattributed_records += 1
            continue
        record_key = record.get("_record_key", "")
        query_keys = attribution.get("query_ids") or attribution.get("query_texts") or attribution.get("query_groups") or attribution.get("discovery_providers") or attribution.get("discovery_channels") or []
        if not query_keys:
            query_keys = ["unknown"]
        for query_key in query_keys:
            bucket = query_buckets.setdefault(
                str(query_key).strip() or "unknown",
                {
                    "query_id": "",
                    "query_text": "",
                    "query_category": "",
                    "discovery_channels": [],
                    "discovery_providers": [],
                    "original_discovery_urls": [],
                    "resolved_source_urls": [],
                    "collector_run_ids": [],
                    "attribution_present_count": 0,
                    "records": {},
                    "attempted_dates": set(),
                    "successful_dates": set(),
                },
            )
            query_id, query_text, query_category = _query_summary_label(attribution)
            if not bucket["query_id"]:
                bucket["query_id"] = query_id
            if not bucket["query_text"]:
                bucket["query_text"] = query_text
            if not bucket["query_category"]:
                bucket["query_category"] = query_category
            bucket["discovery_channels"] = _merge_text_lists(bucket["discovery_channels"], attribution.get("discovery_channels"))
            bucket["discovery_providers"] = _merge_text_lists(bucket["discovery_providers"], attribution.get("discovery_providers"))
            bucket["original_discovery_urls"] = _merge_text_lists(bucket["original_discovery_urls"], attribution.get("original_discovery_urls"))
            bucket["resolved_source_urls"] = _merge_text_lists(bucket["resolved_source_urls"], attribution.get("resolved_source_url"))
            bucket["collector_run_ids"] = _merge_text_lists(bucket["collector_run_ids"], attribution.get("collector_run_id"))
            bucket["attribution_present_count"] += 1
            bucket["records"][str(record_key)] = record
            if attribution.get("discovered_at"):
                bucket["attempted_dates"].add(str(attribution["discovered_at"])[:10])
                if _record_status(record) != "duplicate":
                    bucket["successful_dates"].add(str(attribution["discovered_at"])[:10])
    rows: list[dict[str, Any]] = []
    for bucket_key, bucket in sorted(query_buckets.items(), key=lambda item: (-len(item[1]["records"]), item[0])):
        records_for_query = list(bucket["records"].values())
        status_counts = Counter(_record_status(record) for record in records_for_query)
        matched_performance = performance_lookup.get(bucket_key, {})
        if not matched_performance:
            matched_performance = performance_lookup.get(_nonempty(bucket.get("query_id"))) or performance_lookup.get(_nonempty(bucket.get("query_text"))) or {}
        attempted_run_count = matched_performance.get("attempted_run_count", matched_performance.get("runs"))
        successful_run_count = matched_performance.get("successful_run_count", matched_performance.get("candidates_inserted"))
        failed_run_count = matched_performance.get("failed_run_count", matched_performance.get("rejects"))
        discovered_candidate_count = matched_performance.get("discovered_candidate_count", len(records_for_query))
        unique_candidate_count = matched_performance.get("unique_candidate_count", len(records_for_query))
        included_count = matched_performance.get("included_count", int(status_counts.get("included") or 0))
        excluded_count = matched_performance.get("excluded_count", int(status_counts.get("excluded") or 0))
        unresolved_count = matched_performance.get("unresolved_count", int(status_counts.get("unresolved") or 0))
        duplicate_count = matched_performance.get("duplicate_count", int(status_counts.get("duplicate") or 0))
        last_attempted_date = matched_performance.get("last_attempted_date") or (max(bucket["attempted_dates"]) if bucket["attempted_dates"] else None)
        last_successful_date = matched_performance.get("last_successful_date") or (max(bucket["successful_dates"]) if bucket["successful_dates"] else None)
        attribution_state = "recorded" if bucket["attribution_present_count"] else "not_recorded"
        rows.append(
            {
                "query_id": bucket["query_id"] or "unknown",
                "query_text": bucket["query_text"] or "unknown",
                "query_category": bucket["query_category"] or "unknown",
                "attempted_run_count": int(attempted_run_count) if attempted_run_count is not None and str(attempted_run_count) != "" else None,
                "successful_run_count": int(successful_run_count) if successful_run_count is not None and str(successful_run_count) != "" else None,
                "failed_run_count": int(failed_run_count) if failed_run_count is not None and str(failed_run_count) != "" else None,
                "discovered_candidate_count": int(discovered_candidate_count) if discovered_candidate_count is not None and str(discovered_candidate_count) != "" else len(records_for_query),
                "unique_candidate_count": int(unique_candidate_count) if unique_candidate_count is not None and str(unique_candidate_count) != "" else len(records_for_query),
                "included_count": int(included_count) if included_count is not None and str(included_count) != "" else 0,
                "excluded_count": int(excluded_count) if excluded_count is not None and str(excluded_count) != "" else 0,
                "unresolved_count": int(unresolved_count) if unresolved_count is not None and str(unresolved_count) != "" else 0,
                "duplicate_count": int(duplicate_count) if duplicate_count is not None and str(duplicate_count) != "" else 0,
                "last_attempted_date": last_attempted_date,
                "last_successful_date": last_successful_date,
                "discovery_channels": bucket["discovery_channels"],
                "discovery_providers": bucket["discovery_providers"],
                "original_discovery_urls": bucket["original_discovery_urls"],
                "resolved_source_urls": bucket["resolved_source_urls"],
                "collector_run_ids": bucket["collector_run_ids"],
                "attribution_completeness": round(bucket["attribution_present_count"] / len(records_for_query), 3) if records_for_query else 0.0,
                "attribution_state": attribution_state if bucket["attribution_present_count"] else "not_recorded",
            }
        )
    if unattributed_records:
        rows.append(
            {
                "query_id": "unknown",
                "query_text": "unknown",
                "query_category": "not_recorded",
                "attempted_run_count": None,
                "successful_run_count": None,
                "failed_run_count": None,
                "discovered_candidate_count": 0,
                "unique_candidate_count": 0,
                "included_count": 0,
                "excluded_count": 0,
                "unresolved_count": 0,
                "duplicate_count": 0,
                "last_attempted_date": None,
                "last_successful_date": None,
                "discovery_channels": [],
                "discovery_providers": [],
                "original_discovery_urls": [],
                "resolved_source_urls": [],
                "collector_run_ids": [],
                "attribution_completeness": 0.0,
                "attribution_state": "not_recorded",
                "unattributed_record_count": unattributed_records,
            }
        )
    configured_missing = [query for query in configured_queries if query and query not in {row["query_id"] for row in rows} and query not in {row["query_text"] for row in rows}]
    return {
        "rows": rows,
        "attributed_record_count": len(records) - unattributed_records,
        "unattributed_record_count": unattributed_records,
        "configured_query_count": len(configured_queries),
        "configured_missing_count": len(configured_missing),
        "configured_missing_queries": configured_missing,
        "attribution_completeness": round((len(records) - unattributed_records) / len(records), 3) if records else 0.0,
    }


def build_food_line_coverage_audit(
    root: Path,
    start_date: str,
    end_date: str,
    *,
    benchmark_file: Path | None = None,
) -> dict[str, Any]:
    edition_dates = _complete_date_range(start_date, end_date)
    run_manifests = {date: _load_run_manifest(root, date) for date in edition_dates}
    availability_by_date = {date: _artifact_availability(root, date) for date in edition_dates}
    performance_history = _load_source_performance_history(root)
    query_performance_rows = _load_query_performance_rows(root)
    discovery_queries = _load_discovery_queries(root)

    raw_records: list[dict[str, Any]] = []
    for date in edition_dates:
        raw_records.extend(_load_source_records_for_date(root, date))

    unique_records: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    for record in raw_records:
        key = _record_match_key(record)
        if not key:
            continue
        attribution = _record_query_attribution(record)
        if key in unique_records:
            duplicate_count += 1
            unique_records[key].setdefault("_evidence", []).append(
                {
                    "artifact_kind": record.get("_artifact_kind", ""),
                    "artifact_path": record.get("_artifact_path", ""),
                }
            )
            existing_attribution = unique_records[key].setdefault("_query_attribution", {
                "query_ids": [],
                "query_texts": [],
                "query_groups": [],
                "discovery_channels": [],
                "discovery_providers": [],
                "original_discovery_urls": [],
                "resolved_source_url": "",
                "discovered_at": "",
                "collector_run_id": "",
                "attribution_present": False,
                "attribution_state": "not_recorded",
            })
            existing_attribution["query_ids"] = _merge_text_lists(existing_attribution.get("query_ids"), attribution.get("query_ids"))
            existing_attribution["query_texts"] = _merge_text_lists(existing_attribution.get("query_texts"), attribution.get("query_texts"))
            existing_attribution["query_groups"] = _merge_text_lists(existing_attribution.get("query_groups"), attribution.get("query_groups"))
            existing_attribution["discovery_channels"] = _merge_text_lists(existing_attribution.get("discovery_channels"), attribution.get("discovery_channels"))
            existing_attribution["discovery_providers"] = _merge_text_lists(existing_attribution.get("discovery_providers"), attribution.get("discovery_providers"))
            existing_attribution["original_discovery_urls"] = _merge_text_lists(existing_attribution.get("original_discovery_urls"), attribution.get("original_discovery_urls"))
            if not _nonempty(existing_attribution.get("resolved_source_url")) and _nonempty(attribution.get("resolved_source_url")):
                existing_attribution["resolved_source_url"] = attribution.get("resolved_source_url")
            if not _nonempty(existing_attribution.get("discovered_at")) and _nonempty(attribution.get("discovered_at")):
                existing_attribution["discovered_at"] = attribution.get("discovered_at")
            if not _nonempty(existing_attribution.get("collector_run_id")) and _nonempty(attribution.get("collector_run_id")):
                existing_attribution["collector_run_id"] = attribution.get("collector_run_id")
            existing_attribution["attribution_present"] = bool(
                existing_attribution["query_ids"]
                or existing_attribution["query_texts"]
                or existing_attribution["query_groups"]
                or existing_attribution["discovery_channels"]
                or existing_attribution["discovery_providers"]
                or existing_attribution["original_discovery_urls"]
                or existing_attribution["discovered_at"]
            )
            existing_attribution["attribution_state"] = "recorded" if existing_attribution["attribution_present"] else "not_recorded"
            continue
        unique_records[key] = {
            **record,
            "_record_key": key,
            "_query_attribution": attribution,
            "_evidence": [
                {
                    "artifact_kind": record.get("_artifact_kind", ""),
                    "artifact_path": record.get("_artifact_path", ""),
                }
            ],
        }

    records = list(unique_records.values())
    for record in records:
        attribution = record.get("_query_attribution") or _record_query_attribution(record)
        record["_query_attribution"] = attribution
        record["discovery_query_ids"] = attribution.get("query_ids") or []
        record["discovery_query_texts"] = attribution.get("query_texts") or []
        record["discovery_query_groups"] = attribution.get("query_groups") or []
        record["discovery_channels"] = attribution.get("discovery_channels") or []
        record["discovery_providers"] = attribution.get("discovery_providers") or []
        record["original_discovery_urls"] = attribution.get("original_discovery_urls") or []
        record["resolved_source_url"] = attribution.get("resolved_source_url") or ""
        record["collector_run_ids"] = [attribution.get("collector_run_id")] if _nonempty(attribution.get("collector_run_id")) else []
    included_records = [record for record in records if _record_status(record) == "included"]
    excluded_records = [record for record in records if _record_status(record) == "excluded"]
    unresolved_records = [record for record in records if _record_status(record) == "unresolved"]

    benchmark_rows = _load_benchmarks(root, start_date, end_date, benchmark_file)
    active_benchmarks = [row for row in benchmark_rows if _benchmark_active(row)]
    skipped_benchmarks = [row for row in benchmark_rows if not _benchmark_active(row)]
    deduped_benchmarks: list[dict[str, Any]] = []
    duplicate_benchmark_count = 0
    seen_benchmark_keys: set[str] = set()
    for benchmark in active_benchmarks:
        key = _benchmark_match_key(benchmark)
        if key and key in seen_benchmark_keys:
            duplicate_benchmark_count += 1
            continue
        if key:
            seen_benchmark_keys.add(key)
        deduped_benchmarks.append(benchmark)
    active_benchmarks = deduped_benchmarks

    benchmark_results: list[dict[str, Any]] = []
    discovered_benchmark_count = 0
    included_benchmark_count = 0
    excluded_benchmark_count = 0
    not_discovered_benchmark_count = 0
    indeterminate_benchmark_count = 0

    for benchmark in active_benchmarks:
        matched_records = []
        for record in records:
            matched, match_kind, score = _record_matches_benchmark(benchmark, record)
            if matched:
                matched_records.append((record, match_kind, score))
        matched_records.sort(key=lambda item: (0 if _record_status(item[0]) == "included" else 1, -item[2], item[0].get("_record_key", "")))
        benchmark_date = _record_value(benchmark, "published_at")[:10]
        benchmark_date_available = availability_by_date.get(benchmark_date, {}) if benchmark_date else {}
        artifact_complete = bool(benchmark_date_available.get("completeness_ratio", 0.0) >= 0.5)
        classification = "not_discovered"
        matched_record_summary: dict[str, Any] | None = None
        evidence = {}
        if matched_records:
            discovered_benchmark_count += 1
            matched_record, match_kind, score = matched_records[0]
            matched_record_summary = {
                "record_key": matched_record.get("_record_key"),
                "artifact_kind": matched_record.get("_artifact_kind", ""),
                "artifact_path": matched_record.get("_artifact_path", ""),
                "title": _record_title(matched_record),
                "url": _record_url(matched_record),
                "publisher": _record_value(matched_record, "publisher", "source_name"),
                "source_family": _record_source_family(matched_record),
                "location": _record_location(matched_record),
                "matched_by": match_kind,
                "match_score": round(score, 3),
                "status": _record_status(matched_record),
                "exclusion_reason": _record_classification_reason(matched_record),
                "evidence": matched_record.get("_evidence", []),
            }
            if _record_status(matched_record) == "included":
                included_benchmark_count += 1
                classification = "discovered_and_included"
            elif _record_status(matched_record) == "excluded":
                excluded_benchmark_count += 1
                classification = "discovered_and_excluded"
            else:
                indeterminate_benchmark_count += 1
                classification = "indeterminate because artifacts are missing"
            evidence = matched_record_summary
        else:
            if artifact_complete:
                not_discovered_benchmark_count += 1
                classification = "not_discovered"
            else:
                indeterminate_benchmark_count += 1
                classification = "indeterminate because artifacts are missing"

        benchmark_results.append(
            {
                "title": _record_value(benchmark, "title"),
                "url": _record_value(benchmark, "url"),
                "publisher": _record_value(benchmark, "publisher"),
                "published_at": _record_value(benchmark, "published_at"),
                "reason_expected_to_qualify": _record_value(benchmark, "reason_expected_to_qualify"),
                "review_status": _record_value(benchmark, "review_status"),
                "location": _record_value(benchmark, "location"),
                "pressure_type": _record_value(benchmark, "pressure_type"),
                "notes": _record_value(benchmark, "notes"),
                "classification": classification,
                "matched_record": matched_record_summary,
                "decision_evidence": evidence,
            }
        )

    discovery_totals = {
        "total_unique_records_discovered": len(records),
        "total_included": len(included_records),
        "total_excluded": len(excluded_records),
        "total_unresolved": len(unresolved_records),
        "duplicate_count": duplicate_count,
    }

    query_analysis = _query_analysis_rows(
        records,
        configured_queries=[str(item).strip() for item in (discovery_queries.get("queries") or []) if str(item).strip()],
        performance_rows=query_performance_rows,
    )

    exclusion_analysis = {
        "by_reason": _summarize_rows(excluded_records, lambda row: _record_classification_reason(row) or "unknown"),
        "by_publisher": _summarize_rows(excluded_records, lambda row: _record_value(row, "publisher", "source_name")),
        "by_geography": _summarize_rows(excluded_records, lambda row: _record_location(row)),
        "by_source_family": _summarize_rows(excluded_records, lambda row: _record_source_family(row)),
        "by_pressure_type": _summarize_rows(excluded_records, lambda row: _record_value(row, "pressure_type")),
        "by_discovery_channel": _summarize_rows(excluded_records, lambda row: _record_discovery_channel(row)),
        "by_fetch_or_parse_failure": _summarize_rows(
            [row for row in records if _record_value(row, "fetch_failure_type", "fetch_status", "fetch_error")],
            lambda row: _record_value(row, "fetch_failure_type", "fetch_status", "fetch_error"),
        ),
    }

    match_summary = {
        "approved_benchmark_count": len(active_benchmarks),
        "skipped_benchmark_count": len(skipped_benchmarks),
        "discovered_count": discovered_benchmark_count,
        "included_count": included_benchmark_count,
        "excluded_count": excluded_benchmark_count,
        "not_discovered_count": not_discovered_benchmark_count,
        "indeterminate_count": indeterminate_benchmark_count,
        "discovery_recall": round(discovered_benchmark_count / len(active_benchmarks), 3) if active_benchmarks else 0.0,
        "qualification_recall": round(included_benchmark_count / len(active_benchmarks), 3) if active_benchmarks else 0.0,
        "overall_benchmark_inclusion_rate": round(included_benchmark_count / len(active_benchmarks), 3) if active_benchmarks else 0.0,
        "artifact_completeness": round(
            sum(float(day.get("completeness_ratio") or 0.0) for day in availability_by_date.values()) / len(availability_by_date)
            if availability_by_date
            else 0.0,
            3,
        ),
    }

    recurring_fetch_failures = []
    if isinstance(performance_history, dict):
        for source_id, payload in performance_history.items():
            if not isinstance(payload, dict):
                continue
            fetch_failures = int(payload.get("fetch_failures") or 0)
            runs_seen = int(payload.get("runs_seen") or 0)
            if fetch_failures >= 5 and runs_seen >= 10:
                recurring_fetch_failures.append(
                    {
                        "source_id": source_id,
                        "runs_seen": runs_seen,
                        "fetch_failures": fetch_failures,
                        "last_fetch_error": _nonempty(payload.get("last_fetch_error")),
                        "rolling_quality_score": payload.get("rolling_quality_score"),
                    }
                )
    recurring_fetch_failures.sort(key=lambda row: (-int(row.get("fetch_failures") or 0), str(row.get("source_id") or "")))

    not_discovered = [row for row in benchmark_results if row["classification"] == "not_discovered"]
    discovered_excluded = [row for row in benchmark_results if row["classification"] == "discovered_and_excluded"]
    indeterminate = [row for row in benchmark_results if row["classification"].startswith("indeterminate")]

    gap_analysis = {
        "publisher": _summarize_rows(
            [row for row in benchmark_results if row["classification"] != "discovered_and_included"],
            lambda row: _record_value(row, "publisher") or "unknown",
        ),
        "geography": _summarize_rows(
            [row for row in benchmark_results if row["classification"] != "discovered_and_included"],
            lambda row: _record_value(row, "location") or "unknown",
        ),
        "source_family": _summarize_rows(
            [row for row in benchmark_results if row["classification"] != "discovered_and_included"],
            lambda row: _record_value(row, "pressure_type") or _record_value(row, "source_family") or "unknown",
        ),
        "pressure_type": _summarize_rows(
            [row for row in benchmark_results if row["classification"] != "discovered_and_included"],
            lambda row: _record_value(row, "pressure_type") or "unknown",
        ),
        "discovery_query": [
            {
                "query_id": row.get("query_id") or "unknown",
                "query_text": row.get("query_text") or "unknown",
                "query_category": row.get("query_category") or "unknown",
                "attempted_run_count": row.get("attempted_run_count"),
                "successful_run_count": row.get("successful_run_count"),
                "failed_run_count": row.get("failed_run_count"),
                "discovered_candidate_count": row.get("discovered_candidate_count", 0),
                "unique_candidate_count": row.get("unique_candidate_count", 0),
                "included_count": row.get("included_count", 0),
                "excluded_count": row.get("excluded_count", 0),
                "unresolved_count": row.get("unresolved_count", 0),
                "duplicate_count": row.get("duplicate_count", 0),
                "last_attempted_date": row.get("last_attempted_date"),
                "last_successful_date": row.get("last_successful_date"),
                "attribution_completeness": row.get("attribution_completeness", 0.0),
                "attribution_state": row.get("attribution_state") or "not_recorded",
            }
            for row in (query_analysis.get("rows") or [])[:20]
        ],
        "rss_vs_search": _summarize_rows(
            [row for row in benchmark_results if row["classification"] != "discovered_and_included"],
            lambda row: "rss" if "rss" in _record_value(row, "review_status", "notes").lower() else "search",
        ),
        "canonical_url_behavior": [
            {
                "title": row["title"],
                "url": row["url"],
                "matched_url": row.get("matched_record", {}).get("url", ""),
                "matched_by": row.get("matched_record", {}).get("matched_by", ""),
                "match_score": row.get("matched_record", {}).get("match_score", 0.0),
            }
            for row in benchmark_results
            if row["classification"] != "discovered_and_included" and row.get("matched_record")
        ],
        "fetch_or_parse_failures": recurring_fetch_failures,
    }

    dominant_exclusion_reasons = [row.get("key") for row in exclusion_analysis.get("by_reason", [])[:3] if row.get("key")]
    if not records:
        inclusion_diagnostics = {
            "status": "no_records_discovered",
            "explanation": "No Food Line records were discovered in the selected window.",
            "dominant_exclusion_reasons": [],
            "attribution_state": "not_recorded",
        }
    elif included_records:
        inclusion_diagnostics = {
            "status": "included_records_present",
            "explanation": "At least one discovered record qualified for inclusion.",
            "dominant_exclusion_reasons": dominant_exclusion_reasons,
            "attribution_state": "recorded" if query_analysis.get("attributed_record_count") else "not_recorded",
        }
    elif unresolved_records:
        inclusion_diagnostics = {
            "status": "classification_incomplete",
            "explanation": "No included stories were recorded; discovered records remain unresolved and/or excluded, so inclusion status is indeterminate until classification completes.",
            "dominant_exclusion_reasons": dominant_exclusion_reasons,
            "attribution_state": "recorded" if query_analysis.get("attributed_record_count") else "not_recorded",
        }
    else:
        inclusion_diagnostics = {
            "status": "all_candidates_excluded",
            "explanation": "No included stories were recorded; available diagnostics show the discovered records were all excluded.",
            "dominant_exclusion_reasons": dominant_exclusion_reasons,
            "attribution_state": "recorded" if query_analysis.get("attributed_record_count") else "not_recorded",
        }

    recommendations: list[str] = []
    if not_discovered:
        top_publishers = Counter(row["publisher"] or "unknown" for row in not_discovered).most_common(3)
        if top_publishers:
            recommendations.append(
                "Add or repair discovery coverage for " + ", ".join(f"{publisher} ({count})" for publisher, count in top_publishers)
            )
        top_queries = [item for item in query_analysis.get("rows", []) if int(item.get("discovered_candidate_count") or 0)][:3]
        if top_queries:
            recommendations.append(
                "Add geographic or pressure-language query variants for "
                + ", ".join(str(item.get("query_text") or item.get("query_id") or "unknown") for item in top_queries)
            )
    if query_analysis.get("unattributed_record_count"):
        recommendations.append(
            f"Record query attribution more completely; {int(query_analysis['unattributed_record_count'])} discovered record(s) lack query metadata."
        )
    if excluded_records:
        reasons = Counter(_record_classification_reason(row) or "unknown" for row in excluded_records).most_common(3)
        if reasons:
            recommendations.append(
                "Review recurring exclusion reasons: " + ", ".join(f"{reason} ({count})" for reason, count in reasons)
            )
    if recurring_fetch_failures:
        top_failure = recurring_fetch_failures[0]
        recommendations.append(
            f"Repair or deprioritize repeatedly failing source {top_failure['source_id']} after {top_failure['fetch_failures']} failed runs."
        )
    if gap_analysis["canonical_url_behavior"]:
        recommendations.append("Improve canonical URL handling where the discovered URL differs from the benchmark URL or title match.")
    if not recommendations:
        recommendations.append("No new change recommended; misses appear limited or already explained by current editorial rules.")

    report = {
        "audit_type": "food_line_coverage_audit",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "coverage_window": {
            "start_date": start_date,
            "end_date": end_date,
            "edition_dates_examined": edition_dates,
            "artifact_availability_by_date": availability_by_date,
        },
        "discovery_totals": discovery_totals,
        "query_analysis": query_analysis,
        "inclusion_diagnostics": inclusion_diagnostics,
        "exclusion_analysis": exclusion_analysis,
        "benchmarks": {
            "benchmark_file": str(benchmark_file) if benchmark_file else "",
            "active_benchmark_count": len(active_benchmarks),
            "skipped_benchmark_count": len(skipped_benchmarks),
            "duplicate_benchmark_count": duplicate_benchmark_count,
            "results": benchmark_results,
        },
        "recall_metrics": match_summary,
        "gap_analysis": gap_analysis,
        "recommendations": recommendations,
        "context": {
            "run_manifest_by_date": {
                date: {
                    "edition_date": payload.get("edition_date"),
                    "public_rendered": payload.get("public_rendered"),
                    "excluded_count": payload.get("excluded_count"),
                    "exclusion_reason_summary": payload.get("exclusion_reason_summary"),
                    "primary_signal_status": payload.get("primary_signal_status"),
                    "public_url": payload.get("public_url"),
                }
                for date, payload in run_manifests.items()
                if isinstance(payload, dict)
            },
            "discovery_queries": discovery_queries,
            "source_performance_history_path": str(root / "data" / "dispatches" / "food-line" / "source_performance_history.json"),
            "source_registry_path": str(root / "data" / "dispatches" / "food-line" / "source_registry.json"),
        },
    }
    return report


def render_food_line_coverage_markdown(report: dict[str, Any]) -> str:
    window = report.get("coverage_window") or {}
    totals = report.get("discovery_totals") or {}
    metrics = report.get("recall_metrics") or {}
    lines = [
        f"# Food Line coverage audit - {window.get('start_date')} to {window.get('end_date')}",
        "",
        "## Coverage Window",
        f"- Start date: {window.get('start_date')}",
        f"- End date: {window.get('end_date')}",
        f"- Edition dates examined: {', '.join(window.get('edition_dates_examined') or []) or 'none'}",
        f"- Artifact completeness: {metrics.get('artifact_completeness', 0.0):.3f}",
        "",
        "## Discovery Totals",
        f"- Total unique records discovered: {totals.get('total_unique_records_discovered', 0)}",
        f"- Total included: {totals.get('total_included', 0)}",
        f"- Total excluded: {totals.get('total_excluded', 0)}",
        f"- Total unresolved: {totals.get('total_unresolved', 0)}",
        f"- Duplicate count: {totals.get('duplicate_count', 0)}",
        "",
        "## Inclusion Diagnostics",
    ]
    inclusion = report.get("inclusion_diagnostics") or {}
    lines.extend(
        [
            f"- Status: {inclusion.get('status', 'unknown')}",
            f"- Explanation: {inclusion.get('explanation', 'unknown')}",
            f"- Attribution state: {inclusion.get('attribution_state', 'unknown')}",
            f"- Dominant exclusion reasons: {', '.join(inclusion.get('dominant_exclusion_reasons') or []) or 'none'}",
            "",
            "## Recall Metrics",
            f"- Approved benchmarks: {metrics.get('approved_benchmark_count', 0)}",
            f"- Benchmark duplicates skipped: {(report.get('benchmarks') or {}).get('duplicate_benchmark_count', 0)}",
            f"- Discovered benchmark recall: {metrics.get('discovery_recall', 0.0):.3f}",
            f"- Qualification recall: {metrics.get('qualification_recall', 0.0):.3f}",
            f"- Overall benchmark inclusion rate: {metrics.get('overall_benchmark_inclusion_rate', 0.0):.3f}",
            "",
            "## Query Analysis",
            "| Query ID | Query Text | Category | Attempted | Successful | Failed | Discovered | Included | Excluded | Unresolved | Duplicate | Attribution | Last Attempted | Last Successful |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in (report.get("query_analysis") or {}).get("rows") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("query_id") or "unknown").replace("|", "\\|"),
                    str(row.get("query_text") or "unknown").replace("|", "\\|"),
                    str(row.get("query_category") or "unknown").replace("|", "\\|"),
                    str(row.get("attempted_run_count") if row.get("attempted_run_count") is not None else "unknown"),
                    str(row.get("successful_run_count") if row.get("successful_run_count") is not None else "unknown"),
                    str(row.get("failed_run_count") if row.get("failed_run_count") is not None else "unknown"),
                    str(row.get("discovered_candidate_count") or 0),
                    str(row.get("included_count") or 0),
                    str(row.get("excluded_count") or 0),
                    str(row.get("unresolved_count") or 0),
                    str(row.get("duplicate_count") or 0),
                    str(row.get("attribution_state") or "unknown"),
                    str(row.get("last_attempted_date") or "unknown"),
                    str(row.get("last_successful_date") or "unknown"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Benchmark Results",
            "| Title | Classification | Publisher | URL |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in (report.get("benchmarks") or {}).get("results") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("title") or "").replace("|", "\\|"),
                    str(row.get("classification") or ""),
                    str(row.get("publisher") or "").replace("|", "\\|"),
                    str(row.get("url") or "").replace("|", "%7C"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Exclusion Analysis",
        ]
    )
    for label, rows in (report.get("exclusion_analysis") or {}).items():
        if not rows:
            continue
        lines.append(f"- {label}:")
        for row in rows[:5]:
            detail = ", ".join(
                part
                for part in (
                    row.get("key"),
                    f"count={row.get('count')}",
                    row.get("publisher"),
                    row.get("location"),
                    row.get("source_family"),
                )
                if part
            )
            lines.append(f"  - {detail}")
    lines.extend(
        [
            "",
            "## Gap Analysis",
        ]
    )
    for label, rows in (report.get("gap_analysis") or {}).items():
        if isinstance(rows, list) and rows:
            lines.append(f"- {label}:")
            for row in rows[:5]:
                if "query" in row:
                    lines.append(f"  - {row.get('query')} ({row.get('count', 0)})")
                elif "title" in row:
                    lines.append(f"  - {row.get('title')} -> {row.get('matched_by')} / {row.get('match_score')}")
                else:
                    lines.append(f"  - {row.get('key', row)}")
    lines.extend(
        [
            "",
            "## Recommendations",
        ]
    )
    for recommendation in report.get("recommendations") or []:
        lines.append(f"- {recommendation}")
    return "\n".join(lines)


def write_food_line_coverage_audit(root: Path, report: dict[str, Any], start_date: str, end_date: str) -> tuple[Path, Path]:
    output_dir = root / "output" / "review" / "food-line" / "coverage-audits" / f"{start_date}_{end_date}"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "coverage_audit.json"
    markdown_path = output_dir / "coverage_audit.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown_path.write_text(render_food_line_coverage_markdown(report), encoding="utf-8")
    report["report_path"] = str(json_path)
    report["report_markdown_path"] = str(markdown_path)
    return json_path, markdown_path
