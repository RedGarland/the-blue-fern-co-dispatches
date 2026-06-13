from __future__ import annotations

__test__ = False

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.food_line_sources import (  # noqa: E402
    DEFAULT_AFFECTED_GROUP_KEYWORDS,
    DEFAULT_NEGATIVE_KEYWORDS,
    INVALID_XML_ENTITY_RE,
    CURRENT_PRESSURE_EVIDENCE_TERMS,
    DISCOVERY_CONTEXT_TERMS,
    _extract_page_evidence,
    _extract_page_metadata_date,
    _fetch,
    _normalize_source_text,
    _parse_rss_items,
    canonical_url,
    classify_food_line_source_purpose,
    load_food_line_candidate_registry,
    load_food_line_registry,
    load_food_line_source_performance_history,
    resolve_food_line_fetcher,
    validate_date,
)

STATES = ["WA", "OR", "ID", "CA", "TX", "FL", "NY", "PA", "OH", "MS", "KY", "SC"]
VALID_SOURCE_TYPES = {"rss", "page", "api"}
VALID_STATUSES = {"candidate", "tested_good", "tested_weak", "tested_failed", "enabled", "rejected", "promoted"}
PRESSURE_TERMS = list(dict.fromkeys([*DISCOVERY_CONTEXT_TERMS, *CURRENT_PRESSURE_EVIDENCE_TERMS]))
NEGATIVE_TERMS = [
    "recipe",
    "restaurant",
    "menu",
    "festival",
    "gala",
    "chef",
    "cooking",
    "donation drive",
    "volunteer",
]
GAP_QUERY_RSS_TEMPLATE = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
GAP_TRACKING_QUERY_PARAMS = {
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must be a list")
    return [row for row in payload if isinstance(row, dict)]


def _write_json(path: Path, payload: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_json_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _nonempty(value: Any) -> str:
    return str(value or "").strip()


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_url(url: str) -> str:
    value = _nonempty(url)
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return canonical_url(value)
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "source"


def _discovery_queries_path(root: Path) -> Path:
    return root / "data" / "dispatches" / "food-line" / "source_discovery_queries.json"


def _discovery_blocklist_path(root: Path) -> Path:
    return root / "data" / "dispatches" / "food-line" / "source_discovery_blocklist.json"


def _discovery_priority_path(root: Path) -> Path:
    return root / "data" / "dispatches" / "food-line" / "source_discovery_priority_domains.json"


def _query_metrics_path(root: Path) -> Path:
    return root / "data" / "dispatches" / "food-line" / "source_discovery_query_performance.json"


def _load_food_line_registry_rows(root: Path) -> list[dict[str, Any]]:
    registry_dir = root / "data" / "dispatches" / "food-line"
    paths = [
        registry_dir / "source_registry.json",
        registry_dir / "pressure_source_registry.json",
        registry_dir / "candidate_source_registry.json",
    ]
    rows: list[dict[str, Any]] = []
    for path in paths:
        if path.exists():
            rows.extend(_read_json_list(path))
    return rows


def load_food_line_source_discovery_queries(root: Path) -> list[dict[str, Any]]:
    path = _discovery_queries_path(root)
    repo_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "source_discovery_queries.json"
    if not path.exists():
        path = repo_path
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must be a list")
    normalized: list[dict[str, Any]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        template = _nonempty(row.get("query_template") or row.get("template"))
        if not template:
            continue
        normalized.append(
            {
                "template": template,
                "query_template": template,
                "category": _nonempty(row.get("category")),
                "source_family": _nonempty(row.get("source_family")),
                "runs": int(row.get("runs") or 0),
                "candidates_found": int(row.get("candidates_found") or 0),
                "candidates_inserted": int(row.get("candidates_inserted") or 0),
                "candidates_promoted": int(row.get("candidates_promoted") or 0),
                "candidates_verified_pressure": int(row.get("candidates_verified_pressure") or 0),
                "rejects": int(row.get("rejects") or 0),
                "rolling_query_quality_score": float(row.get("rolling_query_quality_score") or 0),
            }
        )
    return normalized


def _load_discovery_query_rows(root: Path) -> list[dict[str, Any]]:
    path = _discovery_queries_path(root)
    if not path.exists():
        repo_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "source_discovery_queries.json"
        if not repo_path.exists():
            return []
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(repo_path.read_text(encoding="utf-8"), encoding="utf-8")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must be a list")
    normalized: list[dict[str, Any]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        template = _nonempty(row.get("query_template") or row.get("template"))
        if not template:
            continue
        normalized.append(
            {
                "template": template,
                "query_template": template,
                "category": _nonempty(row.get("category")),
                "source_family": _nonempty(row.get("source_family")),
                "runs": int(row.get("runs") or 0),
                "candidates_found": int(row.get("candidates_found") or 0),
                "candidates_inserted": int(row.get("candidates_inserted") or 0),
                "candidates_promoted": int(row.get("candidates_promoted") or 0),
                "candidates_verified_pressure": int(row.get("candidates_verified_pressure") or 0),
                "rejects": int(row.get("rejects") or 0),
                "rolling_query_quality_score": float(row.get("rolling_query_quality_score") or 0),
            }
        )
    return normalized


def _save_discovery_query_rows(root: Path, rows: list[dict[str, Any]]) -> Path:
    path = _discovery_queries_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _discovery_gap_queries_path(root: Path) -> Path:
    return root / "data" / "dispatches" / "food-line" / "discovery_gap_queries.json"


def load_food_line_discovery_gap_queries(root: Path) -> dict[str, Any]:
    path = _discovery_gap_queries_path(root)
    repo_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "discovery_gap_queries.json"
    if not path.exists():
        path = repo_path
    if not path.exists():
        return {"queries": [], "exclude_domains": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must be an object")
    queries = [str(item).strip() for item in payload.get("queries") or [] if str(item).strip()]
    exclude_domains = [str(item).strip().lower() for item in payload.get("exclude_domains") or [] if str(item).strip()]
    return {"queries": queries, "exclude_domains": exclude_domains}


def _gap_query_url(query: str) -> str:
    return GAP_QUERY_RSS_TEMPLATE.format(query=urllib.parse.quote_plus(str(query or "").strip()))


def _gap_domain(url: str) -> str:
    try:
        return urllib.parse.urlsplit(str(url or "").strip()).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""


def _gap_normalize_url(url: str) -> str:
    value = _nonempty(url)
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(_normalize_url(value))
    query_items = [
        (key, val)
        for key, val in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in GAP_TRACKING_QUERY_PARAMS and not key.lower().startswith("utm_")
    ]
    cleaned_query = urllib.parse.urlencode(query_items, doseq=True)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), cleaned_query, ""))


def _gap_parse_published_at(value: str) -> str:
    raw = _nonempty(value)
    if not raw:
        return ""
    for candidate in (raw, raw[:10]):
        try:
            parsed = parsedate_to_datetime(candidate)
        except Exception:  # noqa: BLE001
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            parsed = None
        if parsed is not None:
            return parsed.isoformat()
    return raw


def _gap_parse_rss_items(payload: bytes) -> list[dict[str, str]]:
    text = INVALID_XML_ENTITY_RE.sub("&amp;", payload.decode("utf-8", errors="replace"))
    root = ET.fromstring(text)
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{*}item")
    rows: list[dict[str, str]] = []
    for item in items:
        source_el = item.find("source")
        if source_el is None:
            source_el = item.find("{*}source")
        source_url = _nonempty(source_el.attrib.get("url") if source_el is not None else "")
        publisher = _normalize_source_text(source_el.text or "") if source_el is not None else ""
        link = _nonempty(item.findtext("link") or "")
        if not link:
            link_el = item.find("link")
            if link_el is not None:
                link = _nonempty(link_el.attrib.get("href"))
        candidate_url = source_url or link
        candidate_url = _gap_normalize_url(candidate_url)
        if not candidate_url:
            continue
        rows.append(
            {
                "title": _normalize_source_text(item.findtext("title") or ""),
                "publisher": publisher,
                "publisher_url": source_url,
                "candidate_url": candidate_url,
                "link_url": _gap_normalize_url(link),
                "published_at": _gap_parse_published_at(item.findtext("pubDate") or item.findtext("published") or item.findtext("updated") or ""),
                "summary_or_snippet": _normalize_source_text(item.findtext("description") or item.findtext("summary") or item.findtext("content") or ""),
            }
        )
    return rows


def _gap_text_blob(candidate: dict[str, Any]) -> str:
    return _normalize_source_text(
        " ".join(
            part
            for part in (
                str(candidate.get("title") or ""),
                str(candidate.get("summary_or_snippet") or ""),
                str(candidate.get("publisher") or ""),
                str(candidate.get("candidate_url") or ""),
            )
            if part
        ),
        limit=1200,
    )


def _gap_resource_only_hit(text: str) -> bool:
    lowered = text.lower()
    resource_only_terms = (
        "where to get food",
        "free meals",
        "distribution schedule",
        "hours",
        "locations",
        "find food",
        "find a food bank",
        "get help",
        "apply for benefits",
    )
    return any(term in lowered for term in resource_only_terms)


def score_food_line_discovery_gap_candidate(candidate: dict[str, Any], *, known_local_domain: bool = False) -> tuple[int, list[str], list[str]]:
    text = _gap_text_blob(candidate)
    lowered = text.lower()
    score = 0
    reasons: list[str] = []
    penalties: list[str] = []
    if "food insecurity" in lowered:
        score += 3
        reasons.append("food insecurity")
    if "food bank" in lowered and any(term in lowered for term in ("demand", "shortage", "inventory", "shelves", "cost", "inflation", "snap")):
        score += 3
        reasons.append("food bank pressure")
    if "pantry" in lowered and any(term in lowered for term in ("demand", "shortage", "empty", "line", "shelves")):
        score += 2
        reasons.append("pantry pressure")
    if (
        re.search(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:%|percent)\b", lowered)
        or re.search(r"\$\d{1,3}(?:,\d{3})*(?:\.\d+)?", lowered)
        or re.search(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b", lowered)
    ) and any(term in lowered for term in ("famil", "people", "meal", "dollar", "cost", "county", "counties", "household", "families", "children", "percent", "%")):
        score += 2
        reasons.append("numeric pressure")
    if any(term in lowered for term in ("children", "summer meals", "school meals", "families")):
        score += 2
        reasons.append("households or children")
    if known_local_domain:
        score += 1
        reasons.append("local or news domain")
    if _gap_resource_only_hit(lowered):
        score -= 3
        penalties.append("resource only")
    return score, reasons, penalties


def classify_food_line_discovery_gap_candidate(
    candidate: dict[str, Any],
    *,
    known_status: str,
    known_local_domain: bool = False,
) -> dict[str, Any]:
    score, reasons, penalties = score_food_line_discovery_gap_candidate(candidate, known_local_domain=known_local_domain)
    resource_only = "resource only" in penalties
    if known_status in {"already_included", "already_excluded", "duplicate"}:
        classification = "duplicate_or_known"
    elif score >= 6 and not resource_only:
        classification = "likely_qualifying"
    elif resource_only and score <= 3:
        classification = "likely_resource_only"
    elif score <= 1:
        classification = "likely_resource_only"
    else:
        classification = "needs_review"
    reason_bits = list(reasons)
    reason_bits.extend(penalties)
    if known_status == "already_included":
        reason_bits.append("already included")
    elif known_status == "already_excluded":
        reason_bits.append("already excluded")
    elif known_status == "duplicate":
        reason_bits.append("duplicate")
    elif known_status == "known_domain_new_article":
        reason_bits.append("known domain new article")
    elif known_status == "unknown_domain_new_article":
        reason_bits.append("unknown domain new article")
    return {
        "classification": classification,
        "score": score,
        "reason": "; ".join(reason_bits) if reason_bits else "no strong pressure markers",
        "known_status": known_status,
    }


def _gap_markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_None._"
    lines = [
        "| Title | Publisher/domain | URL | Query | Score | Reason | Known status |",
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for row in rows:
        publisher = _normalize_source_text(str(row.get("publisher") or "")).replace("|", "\\|")
        publisher_domain = _normalize_source_text(str(row.get("publisher_domain") or "")).replace("|", "\\|")
        publisher_cell = publisher
        if publisher_domain and publisher_domain.lower() not in publisher.lower():
            publisher_cell = f"{publisher} ({publisher_domain})" if publisher else publisher_domain
        lines.append(
            "| "
            + " | ".join(
                (
                    publisher_cell
                    if field == "publisher_domain"
                    else _normalize_source_text(str(row.get(field) or "")).replace("|", "\\|")
                )
                for field in ("title", "publisher_domain", "url", "discovered_query", "score", "reason", "known_status")
            )
            + " |"
        )
    return "\n".join(lines)


def _gap_known_status_sets(root: Path) -> dict[str, set[str]]:
    registry_rows = load_food_line_registry(root)
    candidate_rows = load_food_line_candidate_registry(root)
    priority = _load_discovery_priority(root)
    priority_domains = {str(item).strip().lower() for item in priority.get("priority_domains") or [] if str(item).strip()}
    included_urls: set[str] = set()
    excluded_urls: set[str] = set()
    known_urls: set[str] = set()
    known_domains: set[str] = set(priority_domains)
    known_publishers: set[str] = set()
    for row in registry_rows:
        url = _gap_normalize_url(_nonempty(row.get("url") or row.get("candidate_url")))
        if url:
            known_urls.add(url)
            known_domains.add(_gap_domain(url))
            if _truthy(row.get("enabled"), default=True) or str(row.get("status") or "").lower() in {"enabled", "promoted"}:
                included_urls.add(url)
            else:
                excluded_urls.add(url)
        publisher = _nonempty(row.get("publisher") or row.get("source_name") or row.get("name"))
        if publisher:
            known_publishers.add(publisher.lower())
    for row in candidate_rows:
        url = _gap_normalize_url(_nonempty(row.get("candidate_url") or row.get("url")))
        if url:
            known_urls.add(url)
            known_domains.add(_gap_domain(url))
            status = str(row.get("status") or "").strip().lower()
            if status in {"rejected", "quarantined", "archived", "tested_failed"}:
                excluded_urls.add(url)
        publisher = _nonempty(row.get("publisher") or row.get("source_name") or row.get("name"))
        if publisher:
            known_publishers.add(publisher.lower())
    return {
        "included_urls": included_urls,
        "excluded_urls": excluded_urls,
        "known_urls": known_urls,
        "known_domains": {domain for domain in known_domains if domain},
        "known_publishers": known_publishers,
    }


def run_food_line_discovery_gap_check(
    root: Path,
    date: str,
    *,
    fetcher: Any | None = None,
    max_results_per_query: int = 10,
) -> dict[str, Any]:
    edition_date = validate_date(date)
    config = load_food_line_discovery_gap_queries(root)
    query_terms = list(config.get("queries") or [])
    exclude_domains = {str(item).strip().lower() for item in config.get("exclude_domains") or [] if str(item).strip()}
    fetch = resolve_food_line_fetcher(fetcher)
    known = _gap_known_status_sets(root)
    query_errors: list[dict[str, str]] = []
    raw_candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    discovered_at = _utc_now()
    for query in query_terms:
        rss_url = _gap_query_url(query)
        payload, fetch_error = _fetch_url(fetch, rss_url)
        if fetch_error or not payload:
            query_errors.append({"query": query, "url": rss_url, "error": fetch_error or "empty response"})
            continue
        try:
            rss_items = _gap_parse_rss_items(payload)
        except Exception as exc:  # noqa: BLE001
            query_errors.append({"query": query, "url": rss_url, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for item in rss_items[:max_results_per_query]:
            candidate_url = _gap_normalize_url(_nonempty(item.get("candidate_url")))
            if not candidate_url:
                continue
            publisher = _nonempty(item.get("publisher") or _gap_domain(candidate_url))
            publisher_url = _gap_normalize_url(_nonempty(item.get("publisher_url")))
            normalized_url = candidate_url
            known_status = "unknown_domain_new_article"
            if normalized_url in known["included_urls"]:
                known_status = "already_included"
            elif normalized_url in known["excluded_urls"]:
                known_status = "already_excluded"
            elif normalized_url in seen_urls or normalized_url in known["known_urls"]:
                known_status = "duplicate"
            else:
                candidate_domain = _gap_domain(publisher_url or candidate_url)
                if candidate_domain in exclude_domains or _gap_domain(candidate_url) in exclude_domains:
                    known_status = "already_excluded"
                elif candidate_domain in known["known_domains"] or publisher.lower() in known["known_publishers"]:
                    known_status = "known_domain_new_article"
                else:
                    known_status = "unknown_domain_new_article"
            seen_urls.add(normalized_url)
            raw_candidates.append(
                {
                    "title": _nonempty(item.get("title")),
                    "publisher": publisher,
                    "publisher_domain": _gap_domain(publisher_url or candidate_url) or publisher,
                    "publisher_url": publisher_url,
                    "url": candidate_url,
                    "normalized_url": normalized_url,
                    "discovered_query": query,
                    "discovered_at": discovered_at,
                    "published_at": _nonempty(item.get("published_at")),
                    "summary_or_snippet": _nonempty(item.get("summary_or_snippet")),
                    "known_status": known_status,
                    "query_url": rss_url,
                    "raw_candidate": dict(item),
                }
            )
    grouped: dict[str, dict[str, Any]] = {}
    for candidate in raw_candidates:
        normalized_url = str(candidate.get("normalized_url") or "").strip()
        if not normalized_url:
            continue
        current = grouped.get(normalized_url)
        if current is None:
            current = dict(candidate)
            current["discovered_queries"] = [candidate["discovered_query"]]
            grouped[normalized_url] = current
        else:
            current["discovered_queries"].append(candidate["discovered_query"])
            if current.get("published_at") and not candidate.get("published_at"):
                pass
            elif candidate.get("published_at") and not current.get("published_at"):
                current["published_at"] = candidate.get("published_at")
            if len(str(candidate.get("summary_or_snippet") or "")) > len(str(current.get("summary_or_snippet") or "")):
                current["summary_or_snippet"] = candidate.get("summary_or_snippet")
            if current.get("known_status") == "unknown_domain_new_article" and candidate.get("known_status") != "unknown_domain_new_article":
                current["known_status"] = candidate.get("known_status")
        current["discovered_queries"] = list(dict.fromkeys(current.get("discovered_queries") or []))
    candidates: list[dict[str, Any]] = []
    known_local_domains = known["known_domains"]
    for candidate in grouped.values():
        normalized_url = str(candidate.get("normalized_url") or "").strip()
        candidate_domain = _gap_domain(str(candidate.get("publisher_url") or "")) or _gap_domain(normalized_url)
        classification = classify_food_line_discovery_gap_candidate(
            candidate,
            known_status=str(candidate.get("known_status") or "unknown_domain_new_article"),
            known_local_domain=bool(candidate_domain and candidate_domain in known_local_domains),
        )
        row = {
            "title": candidate.get("title") or "",
            "publisher": candidate.get("publisher") or "",
            "publisher_domain": candidate.get("publisher_domain") or candidate_domain or "",
            "url": normalized_url,
            "normalized_url": normalized_url,
            "discovered_query": candidate.get("discovered_queries", [candidate.get("discovered_query") or ""])[0] or "",
            "discovered_queries": candidate.get("discovered_queries") or [],
            "discovered_at": candidate.get("discovered_at") or discovered_at,
            "published_at": candidate.get("published_at") or "",
            "summary_or_snippet": candidate.get("summary_or_snippet") or "",
            "known_status": str(candidate.get("known_status") or "unknown_domain_new_article"),
            "score": classification["score"],
            "reason": classification["reason"],
            "classification": classification["classification"],
        }
        candidates.append(row)
    candidates.sort(key=lambda row: (row["classification"], -int(row["score"] or 0), str(row["title"] or ""), str(row["url"] or "")))
    grouped_by_class = {
        "likely_qualifying": [row for row in candidates if row["classification"] == "likely_qualifying"],
        "needs_review": [row for row in candidates if row["classification"] == "needs_review"],
        "likely_resource_only": [row for row in candidates if row["classification"] == "likely_resource_only"],
        "duplicate_or_known": [row for row in candidates if row["classification"] == "duplicate_or_known"],
    }
    report_dir = root / "data" / "dispatches" / "food-line" / "discovery_gap" / edition_date
    report_dir.mkdir(parents=True, exist_ok=True)
    report_json_path = report_dir / "discovery_gap_report.json"
    report_md_path = report_dir / "discovery_gap_report.md"
    report = {
        "date": edition_date,
        "generated_at": discovered_at,
        "query_source": "google_news_rss",
        "query_count": len(query_terms),
        "queries": query_terms,
        "exclude_domains": sorted(exclude_domains),
        "candidate_count": len(candidates),
        "likely_qualifying_count": len(grouped_by_class["likely_qualifying"]),
        "needs_review_count": len(grouped_by_class["needs_review"]),
        "likely_resource_only_count": len(grouped_by_class["likely_resource_only"]),
        "duplicate_or_known_count": len(grouped_by_class["duplicate_or_known"]),
        "query_errors": query_errors,
        "candidates": candidates,
        "summary": {
            "candidates_reviewed": len(candidates),
            "likely_qualifying": len(grouped_by_class["likely_qualifying"]),
            "needs_review": len(grouped_by_class["needs_review"]),
            "already_known": len(grouped_by_class["duplicate_or_known"]),
            "likely_resource_only": len(grouped_by_class["likely_resource_only"]),
        },
    }
    _write_json_object(report_json_path, report)
    md_lines = [
        f"# Food Line Discovery Gap Check — {edition_date}",
        "",
        "## Likely qualifying candidates",
        "",
        _gap_markdown_table(grouped_by_class["likely_qualifying"]),
        "",
        "## Needs review",
        "",
        _gap_markdown_table(grouped_by_class["needs_review"]),
        "",
        "## Likely resource-only",
        "",
        _gap_markdown_table(grouped_by_class["likely_resource_only"]),
        "",
        "## Duplicate or already known",
        "",
        _gap_markdown_table(grouped_by_class["duplicate_or_known"]),
        "",
        "## Summary",
        f"- candidates reviewed: {len(candidates)}",
        f"- likely qualifying: {len(grouped_by_class['likely_qualifying'])}",
        f"- needs review: {len(grouped_by_class['needs_review'])}",
        f"- already known: {len(grouped_by_class['duplicate_or_known'])}",
        f"- likely resource-only: {len(grouped_by_class['likely_resource_only'])}",
    ]
    report_md_path.write_text("\n".join(md_lines).strip() + "\n", encoding="utf-8")
    summary = {
        "ok": True,
        "date": edition_date,
        "candidate_count": len(candidates),
        "likely_qualifying_count": len(grouped_by_class["likely_qualifying"]),
        "needs_review_count": len(grouped_by_class["needs_review"]),
        "likely_resource_only_count": len(grouped_by_class["likely_resource_only"]),
        "duplicate_or_known_count": len(grouped_by_class["duplicate_or_known"]),
        "report_path": str(report_json_path),
        "report_markdown_path": str(report_md_path),
        "query_errors": query_errors,
        "queries": query_terms,
        "query_source": "google_news_rss",
        "published_pages": False,
        "bluesky_posted": False,
    }
    print(json.dumps(summary, indent=2))
    return summary


def _expand_queries(queries: list[dict[str, Any]], states: list[str]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for query in queries:
        template = str(query.get("query_template") or query.get("template") or "")
        for state in states:
            expanded.append(
                {
                    "state": state,
                    "template": template,
                    "query_template": query.get("query_template") or template,
                    "query": template.format(state=state),
                    "category": query.get("category") or "",
                    "source_family": query.get("source_family") or "",
                }
            )
    return expanded


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must be an object")
    return payload


def _load_discovery_blocklist(root: Path) -> dict[str, list[str]]:
    path = _discovery_blocklist_path(root)
    repo_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "source_discovery_blocklist.json"
    if not path.exists():
        path = repo_path
    payload = _load_json_object(path)
    return {
        "blocked_domains": [str(item).strip().lower() for item in payload.get("blocked_domains") or [] if str(item).strip()],
        "blocked_url_patterns": [str(item).strip().lower() for item in payload.get("blocked_url_patterns") or [] if str(item).strip()],
        "blocked_title_patterns": [str(item).strip().lower() for item in payload.get("blocked_title_patterns") or [] if str(item).strip()],
        "blocked_purposes": [str(item).strip().lower() for item in payload.get("blocked_purposes") or [] if str(item).strip()],
    }


def _load_discovery_priority(root: Path) -> dict[str, list[str]]:
    path = _discovery_priority_path(root)
    repo_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "source_discovery_priority_domains.json"
    if not path.exists():
        path = repo_path
    payload = _load_json_object(path)
    return {
        "priority_domains": [str(item).strip().lower() for item in payload.get("priority_domains") or [] if str(item).strip()],
        "priority_source_families": [str(item).strip().lower() for item in payload.get("priority_source_families") or [] if str(item).strip()],
        "priority_states": [str(item).strip().upper() for item in payload.get("priority_states") or [] if str(item).strip()],
    }


def _domain_from_url(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""


def _pattern_hit(text: str, patterns: list[str]) -> bool:
    lowered = text.lower()
    return any(pattern and pattern in lowered for pattern in patterns)


def _blocked_by_discovery_rules(candidate: dict[str, Any], blocklist: dict[str, list[str]]) -> tuple[bool, str]:
    url = str(candidate.get("candidate_url") or candidate.get("url") or "")
    title = " ".join(
        part
        for part in (
            str(candidate.get("source_name") or ""),
            str(candidate.get("candidate_reason") or ""),
            str(candidate.get("notes") or ""),
        )
        if part
    )
    source_purpose = str(candidate.get("source_purpose") or "unknown").strip().lower()
    domain = _domain_from_url(url)
    if source_purpose in {purpose.lower() for purpose in blocklist.get("blocked_purposes") or []}:
        return True, f"blocked source purpose: {source_purpose}"
    if domain and any(blocked in domain for blocked in blocklist.get("blocked_domains") or []):
        return True, f"blocked domain: {domain}"
    if _pattern_hit(url.lower(), blocklist.get("blocked_url_patterns") or []):
        return True, "blocked url pattern"
    if _pattern_hit(title.lower(), blocklist.get("blocked_title_patterns") or []):
        return True, "blocked title pattern"
    return False, ""


def _priority_bonus(candidate: dict[str, Any], priority: dict[str, list[str]]) -> int:
    bonus = 0
    url = str(candidate.get("candidate_url") or candidate.get("url") or "").lower()
    domain = _domain_from_url(url)
    family = str(candidate.get("source_family") or "").strip().lower()
    state = str(candidate.get("state") or "").strip().upper()
    priority_domains = {item.lower() for item in priority.get("priority_domains") or [] if item}
    if domain and (domain in priority_domains or any(domain.endswith(f".{item}") for item in priority_domains)):
        bonus += 15
    if family in {item.lower() for item in priority.get("priority_source_families") or [] if item}:
        bonus += 10
    if state in {item.upper() for item in priority.get("priority_states") or [] if item}:
        bonus += 5
    source_id = str(candidate.get("source_id") or "").strip().lower()
    if source_id == "miami-herald-local-news":
        bonus += 30
    return bonus


def _query_quality_score(row: dict[str, Any]) -> float:
    runs = int(row.get("runs") or 0)
    inserted = int(row.get("candidates_inserted") or 0)
    verified = int(row.get("candidates_verified_pressure") or 0)
    promoted = int(row.get("candidates_promoted") or 0)
    rejects = int(row.get("rejects") or 0)
    found = int(row.get("candidates_found") or 0)
    score = 0.0
    if runs:
        score += min(50.0, (inserted / runs) * 30.0)
        score += min(20.0, (verified / runs) * 20.0)
        score += min(10.0, (promoted / runs) * 10.0)
        score += min(10.0, (found / runs) * 5.0)
        score -= min(40.0, (rejects / runs) * 8.0)
    return max(0.0, min(100.0, round(score, 2)))


def _query_recommendation(row: dict[str, Any]) -> str:
    score = float(row.get("rolling_query_quality_score") or 0)
    runs = int(row.get("runs") or 0)
    if runs >= 3 and score < 20:
        return "skip"
    if score >= 55 or int(row.get("candidates_verified_pressure") or 0) > 0:
        return "prioritize"
    return "keep"


def _template_terms(template: str) -> list[str]:
    raw = re.sub(r"\{[^}]+\}", " ", template.lower())
    terms = []
    for token in re.findall(r"[a-z0-9]+", raw):
        if token in {"rss", "alert", "news", "update", "updates", "feed"}:
            continue
        if len(token) < 3:
            continue
        if token not in terms:
            terms.append(token)
    return terms


def _find_terms(text: str, terms: list[str]) -> list[str]:
    lowered = text.lower()
    hits = []
    for term in terms:
        if term and term.lower() in lowered and term not in hits:
            hits.append(term)
    return hits


def _resolve_url(base_url: str, href: str) -> str:
    href = html.unescape(str(href or "").strip())
    if not href:
        return ""
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("//"):
        parsed = urllib.parse.urlsplit(base_url)
        return f"{parsed.scheme}:{href}"
    return urllib.parse.urljoin(base_url, href)


def _is_article_like_url(url: str, *, seed_url: str = "", label: str = "") -> bool:
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path.rstrip("/")
    if not path:
        return False
    lowered = f"{url.lower()} {label.lower()}"
    if any(token in lowered for token in ("#comment", "/tag/", "/category/", "/author/", "/search?", "/feed", "/rss", "/atom", "javascript:", "mailto:", "/wp-json/")):
        return False
    if url.rstrip("/") == str(seed_url or "").strip().rstrip("/"):
        return False
    if re.search(r"/20\d{2}[-/]\d{2}[-/]\d{2}/", path):
        return True
    if re.search(r"/regional-news/20\d{2}-\d{2}-\d{2}/", path):
        return True
    if re.search(r"/regional-news/\d{4}-\d{2}-\d{2}/", path):
        return True
    if re.search(r"/\d{4}/\d{2}/\d{2}/", path):
        return True
    return any(term in lowered for term in ("food bank", "food pantry", "snap", "wic", "food insecurity", "food assistance", "increased need", "rising demand"))


def _rank_discovered_link(link: dict[str, str], *, pressure_terms: list[str], query_terms: list[str], seed_url: str) -> int:
    url = str(link.get("url") or "").strip().lower()
    label = str(link.get("label") or "").strip().lower()
    kind = str(link.get("kind") or "").strip().lower()
    score = 0
    if url and url != seed_url.rstrip("/"):
        score += 5
    if kind == "sitemap":
        score += 10
    if re.search(r"/regional-news/20\d{2}-\d{2}-\d{2}/", url):
        score += 60
    if re.search(r"/20\d{2}/\d{2}/\d{2}/", url):
        score += 50
    if any(term in url for term in ("food-bank", "food-banks", "food pantry", "food-pantries", "snap", "wic", "food insecurity", "food assistance", "demand", "need", "federal cut", "increased")):
        score += 20
    if any(term.lower() in label for term in ("food", "snap", "wic", "pantry", "demand", "need", "increased", "banks")):
        score += 15
    if any(term.lower() in url for term in pressure_terms[:10]):
        score += 10
    if any(term.lower() in label for term in query_terms[:10]):
        score += 8
    return score


def _parse_html_links(payload: bytes, base_url: str) -> list[dict[str, str]]:
    text = payload.decode("utf-8", errors="replace")
    results: list[dict[str, str]] = []
    for match in re.finditer(r'<link\b[^>]*rel=["\']alternate["\'][^>]*href=["\']([^"\']+)["\'][^>]*>', text, re.IGNORECASE):
        href = html.unescape(match.group(1)).strip()
        if href.startswith(("http://", "https://")):
            results.append({"url": href, "kind": "rss_or_atom"})
    if re.search(r"<(?:urlset|sitemapindex)\b", text, re.IGNORECASE):
        for match in re.finditer(r"<loc>\s*([^<\s]+)\s*</loc>", text, re.IGNORECASE):
            href = html.unescape(match.group(1)).strip()
            if href.startswith(("http://", "https://")):
                results.append({"url": href, "kind": "sitemap"})
    for match in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, re.IGNORECASE | re.DOTALL):
        href = html.unescape(match.group(1)).strip()
        label = _normalize_source_text(html.unescape(re.sub(r"<[^>]+>", " ", match.group(2))))
        resolved = _resolve_url(base_url, href)
        if not resolved:
            continue
        if _is_article_like_url(resolved, seed_url=base_url, label=label):
            results.append({"url": resolved, "kind": "link", "label": label})
    if "sitemap.xml" in text.lower():
        base = urllib.parse.urlsplit(base_url)
        results.append({"url": urllib.parse.urlunsplit((base.scheme, base.netloc, "/sitemap.xml", "", "")), "kind": "sitemap", "label": ""})
    return results


def _candidate_id(url: str, publisher: str, source_family: str) -> str:
    digest = hashlib.sha1(_normalize_url(url).encode("utf-8")).hexdigest()[:12]
    prefix = _slugify(publisher or source_family or "food-line")
    return f"{prefix}-{digest}"


def _inspect_candidate_page(fetcher: Any, url: str, *, seed_url: str = "") -> dict[str, str]:
    if not url or url == seed_url:
        return {
            "retrieved_at": _utc_now(),
            "published_at": "",
            "page_metadata_date": "",
            "page_title": "",
            "page_summary_or_snippet": "",
            "page_evidence_text": "",
            "page_evidence_text_basis": "",
            "page_fetch_error": "",
        }
    payload, fetch_error = _fetch_url(fetcher, url)
    retrieved_at = _utc_now()
    if fetch_error or not payload:
        return {
            "retrieved_at": retrieved_at,
            "published_at": "",
            "page_metadata_date": "",
            "page_title": "",
            "page_summary_or_snippet": "",
            "page_evidence_text": "",
            "page_evidence_text_basis": "",
            "page_fetch_error": fetch_error,
        }
    evidence = _extract_page_evidence(payload)
    page_metadata_date = _extract_page_metadata_date(payload)
    published_at = page_metadata_date[:10] if page_metadata_date else ""
    return {
        "retrieved_at": retrieved_at,
        "published_at": published_at,
        "page_metadata_date": page_metadata_date,
        "page_title": evidence.get("title") or "",
        "page_summary_or_snippet": evidence.get("summary_or_snippet") or "",
        "page_evidence_text": evidence.get("evidence_text") or "",
        "page_evidence_text_basis": evidence.get("evidence_text_basis") or "",
        "page_fetch_error": "",
    }


def _discovery_seed_rows(root: Path) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for row in _load_food_line_registry_rows(root):
        url = _nonempty(row.get("url") or row.get("candidate_url"))
        if not url:
            continue
        normalized = _normalize_url(url)
        if normalized in seen:
            continue
        seen.add(normalized)
        rows.append(
            {
                "source_id": _nonempty(row.get("source_id")),
                "source_name": _nonempty(row.get("source_name") or row.get("name") or row.get("title") or normalized),
                "publisher": _nonempty(row.get("publisher")),
                "candidate_url": url,
                "source_family": _nonempty(row.get("source_family")),
                "source_type": _nonempty(row.get("source_type") or "page"),
                "state": _nonempty(row.get("state") or "US").upper(),
                "location_name": _nonempty(row.get("location_name") or "United States"),
                "location_scope": _nonempty(row.get("location_scope") or ("national" if _nonempty(row.get("state") or "US").upper() in {"", "US"} else "state_local")),
                "status": _nonempty(row.get("status") or "candidate"),
                "notes": _nonempty(row.get("notes")),
            }
        )
    return rows


def _fetch_url(fetcher: Any, url: str) -> tuple[bytes, str]:
    try:
        payload = fetcher(url, timeout=15)
        return payload, ""
    except Exception as exc:  # noqa: BLE001
        return b"", f"{type(exc).__name__}: {exc}"


def _score_discovery(*, useful_text_available: bool, rss_or_atom_detected: bool, pressure_terms: list[str], negative_terms: list[str], source_type: str, fetched: bool) -> tuple[int, int]:
    score = 0
    if fetched:
        score += 15
    if useful_text_available:
        score += 20
    if rss_or_atom_detected:
        score += 25
    if pressure_terms:
        score += min(30, 10 + 5 * len(pressure_terms))
    if negative_terms:
        score -= min(35, 10 + 5 * len(negative_terms))
    if source_type == "rss":
        score += 10
    score = max(0, min(100, score))
    noise = max(0, min(100, 100 - score))
    return score, noise


def _prefilter_discovery_candidate(
    *,
    source_purpose: str,
    source_type: str,
    useful_text_available: bool,
    rss_or_atom_detected: bool,
    pressure_terms: list[str],
    negative_terms: list[str],
    source_family: str,
    blocked: bool = False,
) -> tuple[bool, str]:
    if blocked:
        return False, "rejected by discovery blocklist"
    if source_purpose in {"donation_page", "evergreen_context", "resource_page", "program_description"}:
        return False, f"rejected by source purpose: {source_purpose}"
    if negative_terms and len(negative_terms) >= 2:
        return False, "rejected by noise: recipe/menu/restaurant/festival content"
    if not useful_text_available and not rss_or_atom_detected and source_type not in {"rss", "api"}:
        return False, "rejected by prefilter: no useful text or feed structure"
    if not pressure_terms and source_family not in {"state_official", "federal_official", "disaster_emergency", "local_news", "public_radio", "nonprofit_news", "food_bank_provider"}:
        return False, "rejected by prefilter: weak source structure"
    return True, ""


def _discovery_quality_score(
    *,
    discovery_score: int,
    source_type: str,
    useful_text_available: bool,
    pressure_terms: list[str],
    negative_terms: list[str],
    source_family: str,
    priority_bonus: int = 0,
) -> tuple[int, dict[str, int]]:
    purpose_score = 20 if source_family in {"local_news", "public_radio", "nonprofit_news", "food_bank_provider", "state_official", "federal_official", "disaster_emergency"} else 5
    text_quality_score = 25 if useful_text_available else 5
    pressure_topic_score = min(25, len(pressure_terms) * 5)
    noise_score = max(0, 30 - len(negative_terms) * 6)
    priority_adjustment = priority_bonus if priority_bonus else -45
    source_quality_score = max(0, min(100, int(round((discovery_score * 0.4) + purpose_score + text_quality_score + pressure_topic_score + noise_score + priority_adjustment))))
    return source_quality_score, {
        "purpose_score": purpose_score,
        "text_quality_score": text_quality_score,
        "pressure_topic_score": pressure_topic_score,
        "noise_score": noise_score,
        "priority_bonus": priority_bonus,
    }


def _normalize_candidate_status(value: Any, default: str = "candidate") -> str:
    status = str(value or default or "candidate").strip().lower()
    return status if status in {"candidate", "tested_good", "tested_weak", "tested_failed", "enabled", "rejected", "quarantined", "archived", "promoted"} else default


def _source_quality_tier(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 45:
        return "medium"
    if score >= 15:
        return "low"
    return "quarantine"


def _candidate_fields_from_discovery(
    *,
    discovered_url: str,
    source_name: str,
    publisher: str,
    source_family: str,
    source_type: str,
    state: str,
    location_name: str,
    location_scope: str,
    reason: str,
    pressure_terms: list[str],
    notes: str,
    source_purpose: str,
    current_or_evergreen: str,
    promotable: bool,
    non_promotable_reason: str,
    source_quality_score: int,
    source_quality_tier: str,
    auto_discovered: bool,
    first_discovered_at: str,
    last_discovered_at: str,
    discovery_count: int,
    last_recommendation: str,
    last_recommendation_reason: str,
    source_seed_url: str = "",
    discovery_seed_url: str = "",
    discovered_from: str = "",
    retrieved_at: str = "",
    published_at: str = "",
    page_metadata_date: str = "",
    evidence_text: str = "",
    evidence_text_basis: str = "",
) -> dict[str, Any]:
    return {
        "source_id": _candidate_id(discovered_url, publisher, source_family),
        "source_name": source_name or publisher or discovered_url,
        "publisher": publisher or source_name,
        "candidate_url": discovered_url,
        "source_seed_url": source_seed_url or discovery_seed_url or "",
        "discovery_seed_url": discovery_seed_url or source_seed_url or "",
        "discovered_from": discovered_from or "",
        "source_family": source_family or "local_news",
        "source_type": source_type,
        "state": state or "US",
        "location_name": location_name or ("United States" if (state or "US").upper() in {"", "US"} else state),
        "location_scope": location_scope or ("national" if (state or "US").upper() in {"", "US"} else "state_local"),
        "candidate_reason": reason,
        "expected_text_basis": "rss_summary" if source_type == "rss" else "page_text",
        "extraction_quality_guess": "high" if source_type == "rss" else "medium",
        "pressure_topics_expected": pressure_terms,
        "status": "candidate",
        "notes": notes,
        "source_purpose": source_purpose,
        "current_or_evergreen": current_or_evergreen,
        "promotable": promotable,
        "non_promotable_reason": non_promotable_reason,
        "source_quality_score": source_quality_score,
        "source_quality_tier": source_quality_tier,
        "auto_discovered": auto_discovered,
        "first_discovered_at": first_discovered_at,
        "last_discovered_at": last_discovered_at,
        "discovery_count": discovery_count,
        "last_recommendation": last_recommendation,
        "last_recommendation_reason": last_recommendation_reason,
        "retrieved_at": retrieved_at,
        "published_at": published_at,
        "page_metadata_date": page_metadata_date,
        "evidence_text": evidence_text,
        "evidence_text_basis": evidence_text_basis,
        "test_count": 0,
        "enable_count": 0,
        "reject_count": 0,
        "keep_candidate_count": 0,
    }


def _merge_candidate(existing: dict[str, Any], discovered: dict[str, Any], discovery_meta: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    preserve_status = _nonempty(existing.get("status"))
    if preserve_status in {"enabled", "rejected", "promoted"}:
        merged["status"] = preserve_status
    for key in (
        "source_name",
        "publisher",
        "candidate_url",
        "source_seed_url",
        "discovery_seed_url",
        "discovered_from",
        "source_family",
        "source_type",
        "state",
        "location_name",
        "location_scope",
        "candidate_reason",
        "expected_text_basis",
        "extraction_quality_guess",
        "notes",
    ):
        if not _nonempty(merged.get(key)) and _nonempty(discovered.get(key)):
            merged[key] = discovered[key]
    if not _nonempty(merged.get("status")):
        merged["status"] = discovered.get("status") or "candidate"
    merged["pressure_topics_expected"] = discovered.get("pressure_topics_expected") or merged.get("pressure_topics_expected") or []
    for key in (
        "discovery_method",
        "discovery_query",
        "discovered_at",
        "discovery_score",
        "url_status",
        "rss_or_atom_detected",
        "useful_text_available",
        "likely_noise_level",
        "preliminary_pressure_terms_found",
        "preliminary_negative_terms_found",
        "source_purpose",
        "current_or_evergreen",
        "promotable",
        "non_promotable_reason",
        "source_quality_score",
        "source_quality_tier",
        "auto_discovered",
        "first_discovered_at",
        "last_discovered_at",
        "last_recommendation",
        "last_recommendation_reason",
        "retrieved_at",
        "published_at",
        "page_metadata_date",
        "evidence_text",
        "evidence_text_basis",
    ):
        if key in discovery_meta:
            merged[key] = discovery_meta[key]
    for key in ("discovery_count", "test_count", "enable_count", "reject_count", "keep_candidate_count"):
        if key in discovery_meta:
            merged[key] = discovery_meta[key]
    return merged


def _discover_candidates_from_seed(
    seed: dict[str, Any],
    queries: list[dict[str, Any]],
    *,
    fetcher: Any,
    max_results_per_query: int,
    blocklist: dict[str, list[str]],
    priority: dict[str, list[str]],
) -> list[dict[str, Any]]:
    seed_url = _nonempty(seed.get("candidate_url"))
    if not seed_url:
        return []
    payload, fetch_error = _fetch_url(fetcher, seed_url)
    discovered: list[dict[str, Any]] = []
    if fetch_error:
        purpose_info = classify_food_line_source_purpose(seed)
        discovered.append(
            {
                "source_id": seed.get("source_id") or _candidate_id(seed_url, seed.get("publisher") or "", seed.get("source_family") or ""),
                "source_name": seed.get("source_name") or seed.get("publisher") or seed_url,
                "publisher": seed.get("publisher") or "",
                "candidate_url": seed_url,
                "source_family": seed.get("source_family") or "local_news",
                "source_type": seed.get("source_type") or "page",
                "state": seed.get("state") or "US",
                "location_name": seed.get("location_name") or "United States",
                "location_scope": seed.get("location_scope") or "national",
                "candidate_reason": f"Discovery fetch failed: {fetch_error}",
                "expected_text_basis": "manual",
                "extraction_quality_guess": "unknown",
                "pressure_topics_expected": [],
                "status": seed.get("status") or "candidate",
                "notes": seed.get("notes") or "",
                "discovery_method": "seed_fetch",
                "discovery_query": "",
                "discovered_at": "",
                "discovery_score": 0,
                "url_status": "error",
                "rss_or_atom_detected": False,
                "useful_text_available": False,
                "likely_noise_level": 100,
                "preliminary_pressure_terms_found": [],
                "preliminary_negative_terms_found": [],
                "source_purpose": purpose_info["source_purpose"],
                "current_or_evergreen": purpose_info["current_or_evergreen"],
                "promotable": purpose_info["promotable"] == "true",
                "non_promotable_reason": purpose_info["non_promotable_reason"],
                "source_quality_score": 0,
                "source_quality_tier": "quarantine",
                "auto_discovered": True,
                "first_discovered_at": _utc_now(),
                "last_discovered_at": _utc_now(),
                "discovery_count": 1,
                "last_recommendation": "rejected_discovery",
                "last_recommendation_reason": fetch_error,
                "inserted_after_prefilter": False,
                "rejected_by_prefilter": True,
                "rejected_by_duplicate": False,
                "rejected_by_source_purpose": purpose_info["source_purpose"] in {"donation_page", "evergreen_context", "resource_page", "program_description"},
                "rejected_by_noise": False,
                "purpose_score": 0,
                "text_quality_score": 0,
                "pressure_topic_score": 0,
                "noise_score": 100,
                "quality_score_components": {"purpose_score": 0, "text_quality_score": 0, "pressure_topic_score": 0, "noise_score": 100},
                "action": "rejected_discovery",
                "reason": fetch_error,
                "raw_diagnostics": {},
            }
        )
        return discovered

    payload_text = payload.decode("utf-8", errors="replace")
    rss_items: list[dict[str, str]] = []
    rss_or_atom_detected = bool(re.search(r"<(?:rss|feed)\b", payload_text, re.IGNORECASE))
    if rss_or_atom_detected:
        try:
            rss_items = _parse_rss_items(payload)
        except Exception:  # noqa: BLE001
            rss_items = []
    page_evidence = _extract_page_evidence(payload)
    discovered_links = _parse_html_links(payload, seed_url)
    query_terms = []
    for query in queries:
        query_terms.extend(_template_terms(query["query"]))
    text_blob = " ".join(
        part for part in (
            page_evidence.get("title") or "",
            page_evidence.get("summary_or_snippet") or "",
            page_evidence.get("evidence_text") or "",
            payload_text[:4000],
        )
        if part
    )
    pressure_terms = _find_terms(text_blob, PRESSURE_TERMS + query_terms)
    negative_terms = _find_terms(text_blob, NEGATIVE_TERMS)
    useful_text_available = bool(_normalize_source_text(text_blob))
    discovered_links = sorted(
        discovered_links,
        key=lambda link: (
            _rank_discovered_link(link, pressure_terms=pressure_terms, query_terms=query_terms, seed_url=seed_url),
            len(str(link.get("url") or "")),
        ),
        reverse=True,
    )
    discovery_score, likely_noise_level = _score_discovery(
        useful_text_available=useful_text_available,
        rss_or_atom_detected=rss_or_atom_detected,
        pressure_terms=pressure_terms,
        negative_terms=negative_terms,
        source_type=_nonempty(seed.get("source_type") or "page"),
        fetched=True,
    )
    discovered_at = _utc_now()
    review_rows: list[dict[str, Any]] = []
    seed_purpose = classify_food_line_source_purpose(seed)
    seed_blocked, seed_block_reason = _blocked_by_discovery_rules(seed, blocklist)

    def add_discovery(
        *,
        discovered_url: str,
        source_name: str,
        source_type: str,
        reason: str,
        query_string: str,
        query_template: str,
        discovery_method: str,
        extra_terms: list[str] | None = None,
        source_purpose: str,
        current_or_evergreen: str,
        promotable: bool,
        non_promotable_reason: str,
    ) -> None:
        nonlocal discovery_score, likely_noise_level
        terms = list(pressure_terms)
        if extra_terms:
            for term in extra_terms:
                if term not in terms:
                    terms.append(term)
        candidate_profile = _inspect_candidate_page(fetcher, discovered_url, seed_url=seed_url)
        discovered_title = candidate_profile.get("page_title") or source_name
        discovered_summary = candidate_profile.get("page_summary_or_snippet") or ""
        discovered_evidence = candidate_profile.get("page_evidence_text") or ""
        discovered_evidence_basis = candidate_profile.get("page_evidence_text_basis") or ("page_text_excerpt" if source_type != "rss" else "rss_item_text")
        blocked, block_reason = _blocked_by_discovery_rules(
            {
                "candidate_url": discovered_url,
                "source_name": discovered_title,
                "candidate_reason": reason,
                "notes": _nonempty(seed.get("notes") or ""),
                "source_purpose": source_purpose,
            },
            blocklist,
        )
        blocked = blocked or seed_blocked
        block_reason = block_reason or seed_block_reason
        priority_bonus = _priority_bonus(
            {
                "candidate_url": discovered_url,
                "source_family": _nonempty(seed.get("source_family") or "local_news"),
                "state": _nonempty(seed.get("state") or "US"),
            },
            priority,
        )
        quality_score, score_components = _discovery_quality_score(
            discovery_score=discovery_score,
            source_type=source_type,
            useful_text_available=useful_text_available,
            pressure_terms=terms,
            negative_terms=negative_terms,
            source_family=_nonempty(seed.get("source_family") or "local_news"),
            priority_bonus=priority_bonus,
        )
        prefilter_allowed, prefilter_reason = _prefilter_discovery_candidate(
            source_purpose=source_purpose,
            source_type=source_type,
            useful_text_available=useful_text_available,
            rss_or_atom_detected=rss_or_atom_detected,
            pressure_terms=terms,
            negative_terms=negative_terms,
            source_family=_nonempty(seed.get("source_family") or "local_news"),
            blocked=blocked,
        )
        high_value_family = _nonempty(seed.get("source_family") or "").strip().lower() in {
            "local_news",
            "public_radio",
            "nonprofit_news",
            "food_bank_provider",
            "state_official",
            "federal_official",
            "disaster_emergency",
            "school_meals_child_nutrition",
            "senior_meals",
        }
        inserted_after_prefilter = bool(prefilter_allowed and (quality_score >= 35 or high_value_family))
        rejected_by_prefilter = not prefilter_allowed
        rejected_by_noise = bool(not inserted_after_prefilter and quality_score < 30 and likely_noise_level >= 70)
        action = "inserted_candidate" if inserted_after_prefilter else "rejected_discovery"
        reason_text = reason if inserted_after_prefilter else (prefilter_reason or block_reason or "insufficient discovery quality")
        candidate = _candidate_fields_from_discovery(
            discovered_url=discovered_url,
            source_name=discovered_title,
            publisher=_nonempty(seed.get("publisher")),
            source_family=_nonempty(seed.get("source_family") or "local_news"),
            source_type=source_type,
            state=_nonempty(seed.get("state") or "US").upper(),
            location_name=_nonempty(seed.get("location_name") or "United States"),
            location_scope=_nonempty(seed.get("location_scope") or "national"),
            reason=reason,
            pressure_terms=terms[:6],
            notes=f"Discovered from {seed_url}",
            source_purpose=source_purpose,
            current_or_evergreen=current_or_evergreen,
            promotable=promotable,
            non_promotable_reason=non_promotable_reason,
            source_quality_score=quality_score,
            source_quality_tier=("high" if quality_score >= 75 else "medium" if quality_score >= 45 else "low" if quality_score >= 15 else "quarantine"),
            auto_discovered=True,
            first_discovered_at=discovered_at,
            last_discovered_at=discovered_at,
            discovery_count=1,
            last_recommendation="candidate",
            last_recommendation_reason=reason_text,
            source_seed_url=seed_url,
            discovery_seed_url=seed_url,
            discovered_from=discovery_method,
            retrieved_at=candidate_profile.get("retrieved_at") or discovered_at,
            published_at=candidate_profile.get("published_at") or "",
            page_metadata_date=candidate_profile.get("page_metadata_date") or "",
            evidence_text=discovered_evidence or discovered_summary,
            evidence_text_basis=discovered_evidence_basis,
        )
        candidate.update(
            {
                "discovery_method": discovery_method,
                "discovery_query": query_string,
                "query_template": query_template,
                "discovered_at": discovered_at,
                "discovery_score": discovery_score,
                "url_status": "ok",
                "rss_or_atom_detected": rss_or_atom_detected,
                "useful_text_available": useful_text_available,
                "likely_noise_level": likely_noise_level,
                "preliminary_pressure_terms_found": terms,
                "preliminary_negative_terms_found": negative_terms,
                "source_purpose": source_purpose,
                "current_or_evergreen": current_or_evergreen,
                "promotable": promotable,
                "non_promotable_reason": non_promotable_reason,
                "source_quality_score": quality_score,
                "source_quality_tier": "high" if quality_score >= 75 else "medium" if quality_score >= 45 else "low" if quality_score >= 15 else "quarantine",
                "auto_discovered": True,
                "first_discovered_at": discovered_at,
                "last_discovered_at": discovered_at,
                "discovery_count": 1,
                "last_recommendation": action,
                "last_recommendation_reason": reason_text,
                "inserted_after_prefilter": inserted_after_prefilter,
                "rejected_by_prefilter": rejected_by_prefilter,
                "rejected_by_duplicate": False,
                "rejected_by_source_purpose": source_purpose in {"donation_page", "evergreen_context", "resource_page", "program_description"},
                "rejected_by_noise": rejected_by_noise,
                "purpose_score": score_components["purpose_score"],
                "text_quality_score": score_components["text_quality_score"],
                "pressure_topic_score": score_components["pressure_topic_score"],
                "noise_score": score_components["noise_score"],
                "priority_bonus": score_components.get("priority_bonus", 0),
                "action": action,
                "reason": reason_text,
                "quality_score_components": score_components,
                "source_seed_url": seed_url,
                "discovery_seed_url": seed_url,
                "discovered_from": discovery_method,
                "retrieved_at": candidate_profile.get("retrieved_at") or discovered_at,
                "published_at": candidate_profile.get("published_at") or "",
                "page_metadata_date": candidate_profile.get("page_metadata_date") or "",
                "evidence_text": discovered_evidence or discovered_summary,
                "evidence_text_basis": discovered_evidence_basis,
            }
        )
        review_rows.append(candidate)

    if rss_items:
        for item in rss_items[:max_results_per_query]:
            item_title = _nonempty(item.get("title")) or page_evidence.get("title") or _nonempty(seed.get("source_name"))
            item_url = _nonempty(item.get("url")) or seed_url
            item_text = " ".join(part for part in (item_title, _nonempty(item.get("summary_or_snippet")), _nonempty(item.get("evidence_text"))) if part)
            item_terms = _find_terms(item_text, PRESSURE_TERMS + query_terms)
            item_negative = _find_terms(item_text, NEGATIVE_TERMS)
            if not item_terms and not item_negative and not useful_text_available:
                continue
            query_string = next((q["query"] for q in queries if q["state"] == _nonempty(seed.get("state") or "US").upper()), "")
            add_discovery(
                discovered_url=item_url if item_url.startswith(("http://", "https://")) else seed_url,
                source_name=item_title,
                source_type="page" if item_url != seed_url else "rss",
                reason=f"Discovered from feed item on {seed.get('source_name') or seed_url}",
                query_string=query_string,
                query_template=next(
                    (
                        q.get("query_template", q.get("template", query_string))
                        for q in queries
                        if q.get("query") == query_string
                    ),
                    query_string,
                ),
                discovery_method="rss_item_link",
                extra_terms=item_terms,
                source_purpose=seed_purpose["source_purpose"],
                current_or_evergreen=seed_purpose["current_or_evergreen"],
                promotable=seed_purpose["promotable"] == "true",
                non_promotable_reason=seed_purpose["non_promotable_reason"],
            )
    if discovered_links:
        for link in discovered_links[:max_results_per_query]:
            link_url = _normalize_url(link["url"])
            if not link_url:
                continue
            title = page_evidence.get("title") or _nonempty(seed.get("source_name") or seed_url)
            query_string = next((q["query"] for q in queries if q["state"] == _nonempty(seed.get("state") or "US").upper()), "")
            add_discovery(
                discovered_url=link_url,
                source_name=title,
                source_type="rss" if link.get("kind") == "rss_or_atom" else "page",
                reason=f"Discovered from {link.get('kind') or 'page link'} on {seed.get('source_name') or seed_url}",
                query_string=query_string,
                query_template=next((q["query_template"] for q in queries if q["query"] == query_string), query_string),
                discovery_method=link.get("kind") or "page_link",
                source_purpose=seed_purpose["source_purpose"],
                current_or_evergreen=seed_purpose["current_or_evergreen"],
                promotable=seed_purpose["promotable"] == "true",
                non_promotable_reason=seed_purpose["non_promotable_reason"],
            )
    if not discovered_links and useful_text_available and (pressure_terms or not negative_terms):
        query_string = next((q["query"] for q in queries if q["state"] == _nonempty(seed.get("state") or "US").upper()), "")
        add_discovery(
            discovered_url=seed_url,
            source_name=page_evidence.get("title") or _nonempty(seed.get("source_name") or seed_url),
            source_type="page" if seed.get("source_type") != "rss" else "rss",
            reason=f"Seed page text supports manual review from {seed.get('source_name') or seed_url}",
            query_string=query_string,
            query_template=next((q["query_template"] for q in queries if q["query"] == query_string), query_string),
            discovery_method="seed_page",
            source_purpose=seed_purpose["source_purpose"],
            current_or_evergreen=seed_purpose["current_or_evergreen"],
            promotable=seed_purpose["promotable"] == "true",
            non_promotable_reason=seed_purpose["non_promotable_reason"],
        )
    if not review_rows:
        review_rows.append(
            {
                "source_id": seed.get("source_id") or _candidate_id(seed_url, seed.get("publisher") or "", seed.get("source_family") or ""),
                "source_name": seed.get("source_name") or seed_url,
                "publisher": seed.get("publisher") or "",
                "candidate_url": seed_url,
                "source_family": seed.get("source_family") or "local_news",
                "source_type": seed.get("source_type") or "page",
                "discovery_method": "seed_page",
                "discovery_query": "",
                "query_template": "",
                "discovery_score": discovery_score,
                "url_status": "ok",
                "rss_or_atom_detected": rss_or_atom_detected,
                "useful_text_available": useful_text_available,
                "likely_noise_level": likely_noise_level,
                "preliminary_pressure_terms_found": pressure_terms,
                "preliminary_negative_terms_found": negative_terms,
                "source_purpose": seed_purpose["source_purpose"],
                "current_or_evergreen": seed_purpose["current_or_evergreen"],
                "promotable": seed_purpose["promotable"] == "true",
                "non_promotable_reason": seed_purpose["non_promotable_reason"],
                "inserted_after_prefilter": False,
                "rejected_by_prefilter": False,
                "rejected_by_duplicate": False,
                "rejected_by_source_purpose": seed_purpose["source_purpose"] in {"donation_page", "evergreen_context", "resource_page", "program_description"},
                "rejected_by_noise": False,
                "source_quality_score": discovery_score,
                "source_quality_tier": _source_quality_tier(discovery_score),
                "purpose_score": 0,
                "text_quality_score": 0,
                "pressure_topic_score": 0,
                "noise_score": likely_noise_level,
                "action": "rejected_discovery" if not pressure_terms and negative_terms else "skipped_duplicate",
                "reason": "insufficient discovery evidence" if not pressure_terms and negative_terms else "duplicate or already represented",
            }
        )
    return review_rows


def discover_food_line_sources(
    root: Path,
    date: str,
    *,
    states: list[str] | None = None,
    max_results_per_query: int = 10,
    max_candidates_total: int = 250,
    max_insertions: int = 100,
    families: list[str] | None = None,
    exclude_families: list[str] | None = None,
    min_source_quality_score: float = 0.0,
    skip_known_bad: bool = True,
    skip_quarantined: bool = True,
    skip_archived: bool = True,
    write_candidates: bool = False,
    dry_run: bool = False,
    fetcher: Any | None = None,
) -> dict[str, Any]:
    states = [state.strip().upper() for state in (states or STATES) if state.strip()]
    family_filter = {family.strip().lower() for family in (families or []) if family.strip()}
    excluded_families = {family.strip().lower() for family in (exclude_families or []) if family.strip()}
    blocklist = _load_discovery_blocklist(root)
    priority = _load_discovery_priority(root)
    query_rows = _load_discovery_query_rows(root)
    expanded_queries = _expand_queries(query_rows, states)
    query_by_template = {row["query_template"]: row for row in query_rows}
    query_results: dict[str, Counter[str]] = {row["query_template"]: Counter() for row in query_rows}
    queries = []
    for query in expanded_queries:
        template = query["template"]
        row = query_by_template.get(template, {})
        rolling_score = float(row.get("rolling_query_quality_score") or 0)
        if int(row.get("runs") or 0) >= 3 and rolling_score < 20:
            continue
        if family_filter and str(query.get("source_family") or "").lower() not in family_filter:
            continue
        if excluded_families and str(query.get("source_family") or "").lower() in excluded_families:
            continue
        queries.append(query)
    fetch = resolve_food_line_fetcher(fetcher)
    discovery_review_path = root / "output" / "review" / "food-line" / date / "source_discovery_review.csv"
    discovery_audit_path = root / "data" / "dispatches" / "food-line" / "sources" / date / "source_discovery_audit.json"
    skipped_known_bad_count = 0
    skipped_quarantined_count = 0
    skipped_archived_count = 0
    seed_rows = _discovery_seed_rows(root)
    seed_rows = sorted(
        seed_rows,
        key=lambda row: (
            _priority_bonus(
                {
                    "candidate_url": _nonempty(row.get("candidate_url") or row.get("url")),
                    "source_family": _nonempty(row.get("source_family")),
                    "state": _nonempty(row.get("state") or "US"),
                },
                priority,
            ),
            1 if _nonempty(row.get("source_family")) in {"public_radio", "nonprofit_news"} else 0,
            1 if _nonempty(row.get("source_id")) in {"nepm-regional-news", "maine-monitor-post-sitemap"} else 0,
        ),
        reverse=True,
    )
    review_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    seen_run_urls: set[str] = set()
    discovered_candidate_rows: list[dict[str, Any]] = []
    discovered_at = _utc_now()
    inserted_count = 0
    updated_count = 0
    skipped_count = 0
    rejected_count = 0
    discovered_candidate_count = 0

    def _seed_allowed(seed: dict[str, Any]) -> tuple[bool, str]:
        nonlocal skipped_known_bad_count, skipped_quarantined_count, skipped_archived_count
        source_family = str(seed.get("source_family") or "").strip().lower()
        status = str(seed.get("status") or "candidate").strip().lower()
        if family_filter and source_family not in family_filter:
            return False, "family filtered"
        if excluded_families and source_family in excluded_families:
            return False, "family excluded"
        if skip_quarantined and status == "quarantined":
            skipped_quarantined_count += 1
            return False, "quarantined candidate skipped"
        if skip_archived and status == "archived":
            skipped_archived_count += 1
            return False, "archived candidate skipped"
        if skip_known_bad and status in {"rejected", "tested_failed"}:
            skipped_known_bad_count += 1
            return False, f"known bad candidate skipped: {status}"
        blocked, reason = _blocked_by_discovery_rules(seed, blocklist)
        if blocked:
            return False, reason
        return True, ""
    eligible_seed_rows: list[dict[str, Any]] = []
    for seed in seed_rows:
        allowed, reason = _seed_allowed(seed)
        if not allowed:
            seed_purpose = classify_food_line_source_purpose(seed)
            review_rows.append(
                {
                    "source_id": _nonempty(seed.get("source_id")),
                    "source_name": _nonempty(seed.get("source_name") or seed.get("name") or seed.get("title")),
                    "publisher": _nonempty(seed.get("publisher")),
                    "candidate_url": _nonempty(seed.get("url") or seed.get("candidate_url")),
                    "state": _nonempty(seed.get("state") or "US").upper(),
                    "source_family": _nonempty(seed.get("source_family")),
                    "source_type": _nonempty(seed.get("source_type") or "page"),
                    "source_purpose": seed_purpose["source_purpose"],
                    "current_or_evergreen": seed_purpose["current_or_evergreen"],
                    "promotable": seed_purpose["promotable"] == "true",
                    "non_promotable_reason": seed_purpose["non_promotable_reason"],
                    "source_quality_score": 0,
                    "source_quality_tier": "low",
                    "action": "rejected_discovery",
                    "reason": reason,
                    "rejected_by_prefilter": "true",
                }
            )
            audit_rows.append(
                {
                    "source_id": _nonempty(seed.get("source_id")),
                    "source_name": _nonempty(seed.get("source_name") or seed.get("name") or seed.get("title")),
                    "candidate_url": _nonempty(seed.get("url") or seed.get("candidate_url")),
                    "action": "rejected_discovery",
                    "reason": reason,
                    "source_purpose": seed_purpose["source_purpose"],
                }
            )
            rejected_count += 1
            continue
        eligible_seed_rows.append(seed)
    seed_rows = eligible_seed_rows
    seed_rows.sort(key=lambda seed: (
        -_priority_bonus(seed, priority),
        str(seed.get("source_family") or ""),
        str(seed.get("source_name") or ""),
    ))
    candidate_registry_path = root / "data" / "dispatches" / "food-line" / "candidate_source_registry.json"
    candidate_registry = _read_json_list(candidate_registry_path)
    candidate_by_source_id = {str(row.get("source_id") or "").strip(): row for row in candidate_registry if _nonempty(row.get("source_id"))}
    candidate_by_url = {_normalize_url(_nonempty(row.get("candidate_url"))): row for row in candidate_registry if _normalize_url(_nonempty(row.get("candidate_url")))}
    candidate_by_pub_url = {
        (str(row.get("publisher") or "").strip().lower(), _normalize_url(_nonempty(row.get("candidate_url")))): row
        for row in candidate_registry
        if _normalize_url(_nonempty(row.get("candidate_url")))
    }
    existing_discovery_urls = set(candidate_by_url.keys())

    def _source_quality_ratio(value: Any) -> float:
        try:
            score = float(value or 0)
        except Exception:  # noqa: BLE001
            score = 0.0
        return score if score <= 1 else score / 100.0

    for seed in seed_rows:
        if discovered_candidate_count >= max_candidates_total or inserted_count >= max_insertions:
            break
        discovery_rows = _discover_candidates_from_seed(
            seed,
            queries,
            fetcher=fetch,
            max_results_per_query=max_results_per_query,
            blocklist=blocklist,
            priority=priority,
        )
        for row in discovery_rows:
            source_id = _nonempty(row.get("source_id"))
            candidate_url = _normalize_url(_nonempty(row.get("candidate_url")))
            publisher = _nonempty(row.get("publisher"))
            pub_url_key = (publisher.lower(), candidate_url)
            query_template = _nonempty(row.get("query_template") or row.get("discovery_query"))
            if not candidate_url or not candidate_url.startswith(("http://", "https://")):
                row["action"] = "rejected_discovery"
                row["reason"] = "candidate_url must use http or https"
                rejected_count += 1
                review_rows.append(row)
                audit_rows.append(row)
                if query_template in query_results:
                    query_results[query_template]["runs"] += 1
                    query_results[query_template]["candidates_found"] += 1
                    query_results[query_template]["rejects"] += 1
                continue
            if _source_quality_ratio(row.get("source_quality_score")) < min_source_quality_score:
                row["action"] = "rejected_discovery"
                row["reason"] = "below minimum source quality score"
                rejected_count += 1
                review_rows.append(row)
                audit_rows.append(row)
                if query_template in query_results:
                    query_results[query_template]["runs"] += 1
                    query_results[query_template]["candidates_found"] += 1
                    query_results[query_template]["rejects"] += 1
                continue
            if candidate_url in seen_run_urls or source_id in {r["source_id"] for r in review_rows if _nonempty(r.get("source_id"))}:
                row["action"] = "skipped_duplicate"
                row["reason"] = "duplicate discovered source"
                skipped_count += 1
                review_rows.append(row)
                audit_rows.append(row)
                if query_template in query_results:
                    query_results[query_template]["runs"] += 1
                    query_results[query_template]["candidates_found"] += 1
                continue
            seen_run_urls.add(candidate_url)
            existing = candidate_by_url.get(candidate_url) or candidate_by_source_id.get(source_id) or candidate_by_pub_url.get(pub_url_key)
            if existing:
                preserved_status = _nonempty(existing.get("status"))
                if preserved_status in {"enabled", "promoted"}:
                    row["action"] = "skipped_existing_enabled"
                    row["reason"] = "enabled candidate preserved"
                    skipped_count += 1
                    review_rows.append(row)
                    audit_rows.append(row)
                    if query_template in query_results:
                        query_results[query_template]["runs"] += 1
                        query_results[query_template]["candidates_found"] += 1
                        query_results[query_template]["rejects"] += 1
                    continue
                if preserved_status in {"rejected", "quarantined", "archived"} and _normalize_url(_nonempty(existing.get("candidate_url") or existing.get("url"))) == candidate_url:
                    row["action"] = "skipped_duplicate"
                    row["reason"] = f"already {preserved_status}"
                    skipped_count += 1
                    if preserved_status == "rejected" and int(existing.get("reject_count") or 0) > 2:
                        existing["status"] = "quarantined"
                        existing["source_quality_tier"] = "quarantine"
                        existing["last_recommendation"] = "quarantined"
                        existing["last_recommendation_reason"] = "rejected more than 2 times"
                    review_rows.append(row)
                    audit_rows.append(row)
                    if query_template in query_results:
                        query_results[query_template]["runs"] += 1
                        query_results[query_template]["candidates_found"] += 1
                        query_results[query_template]["rejects"] += 1
                    continue
                merged = _merge_candidate(existing, row, row)
                merged["candidate_url"] = candidate_url
                merged["source_id"] = _nonempty(existing.get("source_id") or source_id or row["source_id"])
                merged["status"] = _normalize_candidate_status(existing.get("status") or row.get("status") or "candidate")
                merged["auto_discovered"] = bool(existing.get("auto_discovered", True) or row.get("auto_discovered", True))
                merged["discovery_count"] = int(existing.get("discovery_count") or 0) + 1
                merged["last_discovered_at"] = discovered_at
                merged["first_discovered_at"] = _nonempty(existing.get("first_discovered_at")) or discovered_at
                merged["last_recommendation"] = row.get("action") or "updated_candidate"
                merged["last_recommendation_reason"] = row.get("reason") or "existing candidate updated with discovery metadata"
                merged["source_quality_score"] = max(int(existing.get("source_quality_score") or 0), int(row.get("source_quality_score") or 0))
                merged["source_quality_tier"] = row.get("source_quality_tier") or existing.get("source_quality_tier") or "low"
                merged["source_purpose"] = row.get("source_purpose") or existing.get("source_purpose") or "unknown"
                merged["current_or_evergreen"] = row.get("current_or_evergreen") or existing.get("current_or_evergreen") or "unknown"
                merged["promotable"] = row.get("promotable", existing.get("promotable", False))
                merged["non_promotable_reason"] = row.get("non_promotable_reason") or existing.get("non_promotable_reason") or ""
                candidate_by_source_id[merged["source_id"]] = merged
                candidate_by_url[candidate_url] = merged
                candidate_by_pub_url[pub_url_key] = merged
                row["action"] = "updated_candidate"
                row["reason"] = "existing candidate updated with discovery metadata"
                updated_count += 1
                if int(row.get("source_quality_score") or 0) < 30 and not row.get("inserted_after_prefilter"):
                    row["action"] = "rejected_discovery"
                    row["reason"] = row.get("reason") or "insufficient discovery quality"
                    rejected_count += 1
                    review_rows.append(row)
                    audit_rows.append(row)
                    continue
                discovered_candidate_rows.append(merged)
            else:
                if not row.get("inserted_after_prefilter") or int(row.get("source_quality_score") or 0) < 35:
                    row["action"] = "rejected_discovery"
                    row["reason"] = row.get("reason") or "insufficient discovery quality"
                    rejected_count += 1
                    review_rows.append(row)
                    audit_rows.append(row)
                    continue
                row["action"] = "inserted_candidate"
                row["reason"] = "discovered candidate added to registry"
                inserted_count += 1
                row["discovery_count"] = int(row.get("discovery_count") or 1)
                discovered_candidate_rows.append(row)
                candidate_by_source_id[source_id] = row
                candidate_by_url[candidate_url] = row
                candidate_by_pub_url[pub_url_key] = row
            discovered_candidate_count += 1
            review_rows.append(row)
            audit_rows.append({**row, "discovery_queries": queries})
            if query_template in query_results:
                query_results[query_template]["runs"] += 1
                query_results[query_template]["candidates_found"] += 1
                if row.get("action") == "inserted_candidate":
                    query_results[query_template]["candidates_inserted"] += 1
                if row.get("action") == "inserted_candidate" and _source_quality_ratio(row.get("source_quality_score")) >= 0.7:
                    query_results[query_template]["candidates_verified_pressure"] += 0
            if discovered_candidate_count >= max_candidates_total or inserted_count >= max_insertions:
                break

    if write_candidates and not dry_run and discovered_candidate_rows:
        merged_rows = list(candidate_by_source_id.values())
        merged_rows.sort(key=lambda row: str(row.get("source_id") or ""))
        for row in merged_rows:
            row["source_quality_tier"] = str(row.get("source_quality_tier") or _source_quality_tier(int(row.get("source_quality_score") or 0))).lower()
            row["status"] = _normalize_candidate_status(row.get("status"))
            row["auto_discovered"] = bool(row.get("auto_discovered", False))
            row["discovery_count"] = int(row.get("discovery_count") or 0)
        _write_json(candidate_registry_path, merged_rows)

    review_rows.sort(key=lambda row: (str(row.get("action") or ""), str(row.get("source_id") or ""), str(row.get("candidate_url") or "")))
    audit_rows.sort(key=lambda row: (str(row.get("action") or ""), str(row.get("source_id") or ""), str(row.get("candidate_url") or "")))
    _write_csv(
        discovery_review_path,
        [
            "source_id",
            "source_name",
            "publisher",
            "candidate_url",
            "state",
            "source_family",
            "source_type",
            "source_purpose",
            "current_or_evergreen",
            "promotable",
            "non_promotable_reason",
            "inserted_after_prefilter",
            "rejected_by_prefilter",
            "rejected_by_duplicate",
            "rejected_by_source_purpose",
            "rejected_by_noise",
            "source_quality_score",
            "source_quality_tier",
            "purpose_score",
            "text_quality_score",
            "pressure_topic_score",
            "noise_score",
            "priority_bonus",
            "discovery_method",
            "discovery_query",
            "discovery_score",
            "url_status",
            "rss_or_atom_detected",
            "useful_text_available",
            "likely_noise_level",
            "preliminary_pressure_terms_found",
            "preliminary_negative_terms_found",
            "action",
            "reason",
        ],
        review_rows,
    )
    discovery_audit_path.parent.mkdir(parents=True, exist_ok=True)
    discovery_audit_path.write_text(json.dumps(audit_rows, indent=2, ensure_ascii=False), encoding="utf-8")
    updated_query_rows: list[dict[str, Any]] = []
    for row in query_rows:
        template = row["query_template"]
        stats = query_results.get(template, Counter())
        merged = dict(row)
        merged["runs"] = int(merged.get("runs") or 0) + int(stats.get("runs") or 0)
        merged["candidates_found"] = int(merged.get("candidates_found") or 0) + int(stats.get("candidates_found") or 0)
        merged["candidates_inserted"] = int(merged.get("candidates_inserted") or 0) + int(stats.get("candidates_inserted") or 0)
        merged["candidates_promoted"] = int(merged.get("candidates_promoted") or 0) + int(stats.get("candidates_promoted") or 0)
        merged["candidates_verified_pressure"] = int(merged.get("candidates_verified_pressure") or 0) + int(stats.get("candidates_verified_pressure") or 0)
        merged["rejects"] = int(merged.get("rejects") or 0) + int(stats.get("rejects") or 0)
        merged["rolling_query_quality_score"] = _query_quality_score(merged)
        updated_query_rows.append(merged)
    query_performance_path = _save_discovery_query_rows(root, updated_query_rows)
    query_report_path = root / "output" / "review" / "food-line" / "discovery_query_performance_report.csv"
    _write_csv(
        query_report_path,
        [
            "query_template",
            "runs",
            "candidates_found",
            "candidates_inserted",
            "candidates_promoted",
            "candidates_verified_pressure",
            "rejects",
            "rolling_query_quality_score",
            "recommended_action",
        ],
        [
            {
                **row,
                "recommended_action": _query_recommendation(row),
            }
            for row in updated_query_rows
        ],
    )
    history = load_food_line_source_performance_history(root)
    health_rows = []
    latest_review_by_source_id = {str(row.get("source_id") or ""): row for row in review_rows if str(row.get("source_id") or "").strip()}
    for candidate in sorted(candidate_by_source_id.values(), key=lambda row: str(row.get("source_id") or "")):
        source_id = str(candidate.get("source_id") or "").strip()
        history_row = history.get(source_id, {})
        latest_review = latest_review_by_source_id.get(source_id, {})
        source_quality_score = int(candidate.get("source_quality_score") or history_row.get("rolling_quality_score") or 0)
        source_quality_tier = str(candidate.get("source_quality_tier") or "").strip().lower() or _source_quality_tier(source_quality_score)
        useful_text_available = str(latest_review.get("useful_text_available") or "").lower() == "true" or source_quality_score >= 20
        recommended_action = "preserve_enabled" if str(candidate.get("status") or "").lower() == "enabled" else (
            "archive" if int(history_row.get("fetch_failures") or 0) >= 3 and not useful_text_available else (
                "quarantine" if int(candidate.get("reject_count") or 0) >= 2 or int(history_row.get("fetch_failures") or 0) >= 2 else "keep_candidate"
            )
        )
        health_rows.append(
            {
                "source_id": source_id,
                "source_name": candidate.get("source_name") or "",
                "status": candidate.get("status") or "",
                "source_family": candidate.get("source_family") or "",
                "state": candidate.get("state") or "",
                "source_quality_score": source_quality_score,
                "source_quality_tier": source_quality_tier,
                "test_count": int(candidate.get("test_count") or 0),
                "reject_count": int(candidate.get("reject_count") or 0),
                "keep_candidate_count": int(candidate.get("keep_candidate_count") or 0),
                "enable_count": int(candidate.get("enable_count") or 0),
                "fetch_failures": int(history_row.get("fetch_failures") or 0),
                "useful_text_available": str(useful_text_available).lower(),
                "last_recommendation": candidate.get("last_recommendation") or "",
                "recommended_action": recommended_action,
            }
        )
    health_report_path = root / "output" / "review" / "food-line" / "source_registry_health_report.csv"
    _write_csv(
        health_report_path,
        [
            "source_id",
            "source_name",
            "status",
            "source_family",
            "state",
            "source_quality_score",
            "source_quality_tier",
            "test_count",
            "reject_count",
            "keep_candidate_count",
            "enable_count",
            "fetch_failures",
            "useful_text_available",
            "last_recommendation",
            "recommended_action",
        ],
        health_rows,
    )
    summary = {
        "ok": True,
        "discovered_candidate_count": discovered_candidate_count,
        "inserted_count": inserted_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "rejected_count": rejected_count,
        "discovered_count": discovered_candidate_count,
        "prefilter_rejected_count": sum(1 for row in review_rows if str(row.get("action") or "") == "rejected_discovery" and str(row.get("rejected_by_prefilter") or "").lower() == "true"),
        "duplicate_skipped_count": sum(1 for row in review_rows if str(row.get("action") or "") == "skipped_duplicate"),
        "quarantined_skipped_count": sum(1 for row in review_rows if str(row.get("action") or "") == "skipped_duplicate" and str(row.get("reason") or "").startswith("already quarantined")),
        "archived_skipped_count": sum(1 for row in review_rows if str(row.get("action") or "") == "skipped_duplicate" and str(row.get("reason") or "").startswith("already archived")),
        "skipped_known_bad_count": skipped_known_bad_count,
        "skipped_quarantined_count": skipped_quarantined_count,
        "skipped_archived_count": skipped_archived_count,
        "source_quality_tier_counts": dict(sorted(Counter(str(row.get("source_quality_tier") or "quarantine") for row in review_rows).items())),
        "query_performance_path": str(query_performance_path),
        "query_performance_report_path": str(query_report_path),
        "source_registry_health_report_path": str(health_report_path),
        "review_path": str(discovery_review_path),
        "audit_path": str(discovery_audit_path),
        "candidate_registry_path": str(candidate_registry_path),
    }
    print(json.dumps(summary, indent=2))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover Food Line candidate sources")
    parser.add_argument("--date", required=True)
    parser.add_argument("--states", default=",".join(STATES))
    parser.add_argument("--max-results-per-query", type=int, default=10)
    parser.add_argument("--max-candidates-total", type=int, default=250)
    parser.add_argument("--max-insertions", type=int, default=100)
    parser.add_argument("--families", default="")
    parser.add_argument("--exclude-families", default="")
    parser.add_argument("--min-source-quality-score", type=float, default=0.0)
    parser.add_argument("--skip-known-bad", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--skip-quarantined", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--skip-archived", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--write-candidates", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--gap-check", action="store_true", help="Run the Food Line discovery gap diagnostic only.")
    args = parser.parse_args(argv)
    if args.gap_check:
        result = run_food_line_discovery_gap_check(
            ROOT,
            args.date,
            max_results_per_query=args.max_results_per_query,
        )
        return 0 if result.get("ok") else 1
    states = [state.strip().upper() for state in args.states.split(",") if state.strip()]
    families = [family.strip() for family in args.families.split(",") if family.strip()]
    exclude_families = [family.strip() for family in args.exclude_families.split(",") if family.strip()]
    result = discover_food_line_sources(
        ROOT,
        args.date,
        states=states,
        max_results_per_query=args.max_results_per_query,
        max_candidates_total=args.max_candidates_total,
        max_insertions=args.max_insertions,
        families=families or None,
        exclude_families=exclude_families or None,
        min_source_quality_score=args.min_source_quality_score,
        skip_known_bad=args.skip_known_bad,
        skip_quarantined=args.skip_quarantined,
        skip_archived=args.skip_archived,
        write_candidates=args.write_candidates,
        dry_run=args.dry_run,
    )
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
