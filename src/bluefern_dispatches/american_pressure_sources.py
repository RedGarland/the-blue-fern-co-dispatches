from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
import urllib.error
import urllib.request


REGISTRY_PATH = Path("data/dispatches/american-pressure/source_registry.yml")
NATIONAL_REGISTRY_PATH = Path("data/source_registry/american_pressure_sources.json")
NATIONAL_SUMMARY_PATH = Path("output/site/american-pressure/source_coverage_summary.json")
NATIONAL_FEED_HEALTH_PATH = Path("output/site/american-pressure/source_feed_health.json")
NATIONAL_VALIDATION_REPORT_PATH = Path("output/site/american-pressure/source_feed_validation_report.json")
REQUIRED_FIELDS = {
    "source_id", "name", "url", "publisher", "pillar", "geography", "source_type", "reliability_tier", "update_frequency", "enabled", "notes",
}
ALLOWED_SOURCE_STATES = {"enabled", "manual_only", "diagnostics_only", "disabled"}
ALLOWED_PILLARS = {
    "food_pressure",
    "financial_distress_pressure",
    "housing_household_cost_pressure",
    "health_access_pressure",
    "labor_income_pressure",
    "local_system_strain",
    "environmental_pressure",
    "policy_implementation",
}
ALLOWED_RELIABILITY_TIERS = {"official_primary", "institutional", "reputable_reporting", "context_only"}
DEFAULT_TIMEOUT_SECONDS = 8
DEFAULT_USER_AGENT = "BlueFernDispatches/0.1 american-pressure-source-check"
NATIONAL_REQUIRED_FIELDS = {
    "source_id",
    "source_name",
    "homepage_url",
    "rss_url",
    "source_type",
    "coverage_scope",
    "state",
    "metro_or_region",
    "urban_rural_focus",
    "pressure_pillars",
    "reliability_tier",
    "ownership_type",
    "language",
    "active",
    "notes",
    "atom_url",
    "json_feed_url",
    "sitemap_url",
    "feed_discovery_status",
    "feed_type",
    "polling_priority",
    "collection_method",
    "robots_allowed",
    "paywall_status",
    "last_verified_utc",
    "feed_health",
    "ingest_ready",
    "feed_url_known",
    "feed_validated_live",
    "validation_status",
}
NATIONAL_ALLOWED_RELIABILITY_TIERS = {
    "government",
    "public_media",
    "nonprofit_news",
    "established_local_news",
    "institutional_org",
}
NATIONAL_ALLOWED_SOURCE_TYPES = {
    "public_media",
    "nonprofit_newsroom",
    "local_news",
    "government_data",
    "institutional_org",
}
NATIONAL_ALLOWED_URBAN_RURAL = {"urban", "rural", "mixed"}
NATIONAL_ALLOWED_PILLARS = {
    "food_grocery_pressure",
    "housing_utility_pressure",
    "health_care_access_pressure",
    "jobs_paycheck_pressure",
    "debt_bankruptcy_pressure",
    "public_services_under_strain",
    "disaster_insurance_recovery_pressure",
    "benefits_policy_delivery_pressure",
    "transportation_daily_access_pressure",
    "childcare_schools_family_pressure",
}
US_STATES_PLUS_DC = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
    "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}
NATIONAL_ALLOWED_FEED_DISCOVERY_STATUS = {"not_attempted", "discovery_attempted", "feed_validated", "sitemap_only", "manual_only", "failed"}
NATIONAL_ALLOWED_FEED_TYPES = {"rss", "atom", "json_feed", "sitemap", "none"}
NATIONAL_ALLOWED_POLLING_PRIORITIES = {"critical", "high", "medium", "low"}
NATIONAL_ALLOWED_COLLECTION_METHODS = {"feed_polling", "sitemap_polling", "manual_review"}
NATIONAL_ALLOWED_PAYWALL = {"open", "partial", "paywalled", "unknown"}
NATIONAL_ALLOWED_FEED_HEALTH = {"ok", "healthy", "degraded", "unknown", "broken"}
NATIONAL_ALLOWED_VALIDATION_STATUS = {"pending_live_validation", "live_validated", "live_failed", "not_applicable"}


class RegistryValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SourceCheckResult:
    source_id: str
    url: str
    pillar: str
    geography: str
    source_type: str
    reliability_tier: str
    enabled: bool
    validation_ok: bool
    fetch_attempted: bool
    fetch_success: bool | None
    status_code: int | None
    failure_reason: str | None
    checked_at: str
    source_state: str
    recommendation: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_scalar(value: str) -> Any:
    raw = value.strip()
    if not raw:
        return ""
    if raw[0] == raw[-1] and raw[0] in {'"', "'"} and len(raw) >= 2:
        raw = raw[1:-1]
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return raw


def _parse_simple_yaml(text: str) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped == "sources:":
            continue
        if stripped.startswith("- "):
            if current is not None:
                sources.append(current)
            current = {}
            inline = stripped[2:].strip()
            if inline and ":" in inline:
                key, value = inline.split(":", 1)
                current[key.strip()] = _parse_scalar(value)
            continue
        if current is None:
            continue
        if ":" not in stripped:
            raise RegistryValidationError(f"Unsupported YAML line: {raw_line}")
        key, value = stripped.split(":", 1)
        current[key.strip()] = _parse_scalar(value)
    if current is not None:
        sources.append(current)
    return sources


def load_source_registry(root: Path, path: Path | None = None) -> list[dict[str, Any]]:
    registry_path = root / (path or REGISTRY_PATH)
    if not registry_path.exists():
        raise FileNotFoundError(f"American Pressure source registry does not exist: {registry_path}")
    text = registry_path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(text)
        raw = payload.get("sources", []) if isinstance(payload, dict) else payload
        if not isinstance(raw, list):
            raise RegistryValidationError("source_registry.yml must contain a top-level list or a 'sources' list")
        return [item for item in raw if isinstance(item, dict)]
    except Exception:
        return _parse_simple_yaml(text)


def load_national_source_registry(root: Path, path: Path | None = None) -> list[dict[str, Any]]:
    registry_path = root / (path or NATIONAL_REGISTRY_PATH)
    if not registry_path.exists():
        raise FileNotFoundError(f"American Pressure national source registry does not exist: {registry_path}")
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RegistryValidationError("american_pressure_sources.json must be a top-level list")
    return [row for row in payload if isinstance(row, dict)]


def _is_valid_url(url: str) -> bool:
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_registry_sources(sources: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_pillars: set[str] = set()
    for index, source in enumerate(sources, start=1):
        source_id = str(source.get("source_id") or "").strip()
        enabled = source.get("enabled")
        prefix = f"source {index} ({source_id or 'missing-source-id'})"
        if isinstance(enabled, bool) and enabled:
            missing = [field for field in sorted(REQUIRED_FIELDS) if field not in source or str(source.get(field)).strip() == ""]
            if missing:
                errors.append(f"{prefix} missing required fields: {', '.join(missing)}")
        if source_id:
            if source_id in seen_ids:
                errors.append(f"{prefix} has duplicate source_id: {source_id}")
            seen_ids.add(source_id)
        else:
            errors.append(f"{prefix} has empty source_id")
        if not isinstance(enabled, bool):
            errors.append(f"{prefix} has non-boolean enabled value: {enabled!r}")
        pillar = str(source.get("pillar") or "").strip()
        if pillar not in ALLOWED_PILLARS:
            errors.append(f"{prefix} has invalid pillar: {pillar!r}")
        else:
            seen_pillars.add(pillar)
        reliability_tier = str(source.get("reliability_tier") or "").strip()
        if reliability_tier not in ALLOWED_RELIABILITY_TIERS:
            errors.append(f"{prefix} has invalid reliability_tier: {reliability_tier!r}")
        source_type = str(source.get("source_type") or "").strip()
        if not source_type:
            errors.append(f"{prefix} has empty source_type")
        source_state = str(source.get("source_state") or ("enabled" if enabled else "disabled")).strip()
        if source_state not in ALLOWED_SOURCE_STATES:
            errors.append(f"{prefix} has invalid source_state: {source_state!r}")
        if source_state == "enabled" and enabled is not True:
            errors.append(f"{prefix} source_state is enabled but enabled is not true")
        if source_state in {"manual_only", "diagnostics_only", "disabled"} and enabled is True:
            errors.append(f"{prefix} source_state is {source_state} but enabled is true")
        url = str(source.get("url") or "").strip()
        if not _is_valid_url(url):
            errors.append(f"{prefix} has malformed URL: {url!r}")
    missing_pillars = sorted(ALLOWED_PILLARS - seen_pillars)
    if missing_pillars:
        errors.append(f"registry missing required pillars: {', '.join(missing_pillars)}")
    return errors


def validate_national_source_registry(sources: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_feed_urls: set[str] = set()
    states_seen: set[str] = set()
    by_state_type: dict[str, dict[str, int]] = {}
    for index, row in enumerate(sources, start=1):
        prefix = f"national source {index}"
        missing = [field for field in sorted(NATIONAL_REQUIRED_FIELDS) if field not in row]
        if missing:
            errors.append(f"{prefix} missing required fields: {', '.join(missing)}")
            continue
        source_id = str(row.get("source_id") or "").strip()
        if not source_id:
            errors.append(f"{prefix} has empty source_id")
        elif source_id in seen_ids:
            errors.append(f"{prefix} has duplicate source_id: {source_id}")
        else:
            seen_ids.add(source_id)

        homepage_url = str(row.get("homepage_url") or "").strip()
        if not _is_valid_url(homepage_url):
            errors.append(f"{prefix} has malformed homepage_url: {homepage_url!r}")

        for feed_field in ("rss_url", "atom_url", "json_feed_url", "sitemap_url"):
            feed_url = str(row.get(feed_field) or "").strip()
            if not feed_url:
                continue
            if not _is_valid_url(feed_url):
                errors.append(f"{prefix} has malformed {feed_field}: {feed_url!r}")
            if feed_url in seen_feed_urls:
                errors.append(f"{prefix} has duplicate feed URL: {feed_url}")
            seen_feed_urls.add(feed_url)

        source_type = str(row.get("source_type") or "").strip()
        if source_type not in NATIONAL_ALLOWED_SOURCE_TYPES:
            errors.append(f"{prefix} has unsupported source_type: {source_type!r}")

        reliability = str(row.get("reliability_tier") or "").strip()
        if reliability not in NATIONAL_ALLOWED_RELIABILITY_TIERS:
            errors.append(f"{prefix} has invalid reliability_tier: {reliability!r}")

        state = str(row.get("state") or "").strip().upper()
        if state not in US_STATES_PLUS_DC:
            errors.append(f"{prefix} has invalid state: {state!r}")
        else:
            states_seen.add(state)
            type_counts = by_state_type.setdefault(state, {})
            type_counts[source_type] = type_counts.get(source_type, 0) + 1

        urban_rural = str(row.get("urban_rural_focus") or "").strip().lower()
        if urban_rural not in NATIONAL_ALLOWED_URBAN_RURAL:
            errors.append(f"{prefix} has invalid urban_rural_focus: {urban_rural!r}")

        pillars = row.get("pressure_pillars")
        if not isinstance(pillars, list) or not pillars:
            errors.append(f"{prefix} must include non-empty pressure_pillars list")
        else:
            invalid = [str(p) for p in pillars if str(p) not in NATIONAL_ALLOWED_PILLARS]
            if invalid:
                errors.append(f"{prefix} has invalid pressure_pillars: {', '.join(invalid)}")

        if str(row.get("language") or "").strip().lower() != "en":
            errors.append(f"{prefix} has unsupported language (expected 'en')")
        if not isinstance(row.get("active"), bool):
            errors.append(f"{prefix} has non-boolean active field")
        if not isinstance(row.get("robots_allowed"), bool):
            errors.append(f"{prefix} has non-boolean robots_allowed field")
        if not isinstance(row.get("ingest_ready"), bool):
            errors.append(f"{prefix} has non-boolean ingest_ready field")
        if not isinstance(row.get("feed_url_known"), bool):
            errors.append(f"{prefix} has non-boolean feed_url_known field")
        if not isinstance(row.get("feed_validated_live"), bool):
            errors.append(f"{prefix} has non-boolean feed_validated_live field")
        feed_status = str(row.get("feed_discovery_status") or "").strip()
        if feed_status not in NATIONAL_ALLOWED_FEED_DISCOVERY_STATUS:
            errors.append(f"{prefix} has invalid feed_discovery_status: {feed_status!r}")
        feed_type = str(row.get("feed_type") or "").strip()
        if feed_type not in NATIONAL_ALLOWED_FEED_TYPES:
            errors.append(f"{prefix} has invalid feed_type: {feed_type!r}")
        polling_priority = str(row.get("polling_priority") or "").strip()
        if polling_priority not in NATIONAL_ALLOWED_POLLING_PRIORITIES:
            errors.append(f"{prefix} has invalid polling_priority: {polling_priority!r}")
        collection_method = str(row.get("collection_method") or "").strip()
        if collection_method not in NATIONAL_ALLOWED_COLLECTION_METHODS:
            errors.append(f"{prefix} has invalid collection_method: {collection_method!r}")
        paywall_status = str(row.get("paywall_status") or "").strip()
        if paywall_status not in NATIONAL_ALLOWED_PAYWALL:
            errors.append(f"{prefix} has invalid paywall_status: {paywall_status!r}")
        feed_health = str(row.get("feed_health") or "").strip()
        if feed_health not in NATIONAL_ALLOWED_FEED_HEALTH:
            errors.append(f"{prefix} has invalid feed_health: {feed_health!r}")
        validation_status = str(row.get("validation_status") or "").strip()
        if validation_status not in NATIONAL_ALLOWED_VALIDATION_STATUS:
            errors.append(f"{prefix} has invalid validation_status: {validation_status!r}")
        if bool(row.get("feed_validated_live")) and validation_status != "live_validated":
            errors.append(f"{prefix} feed_validated_live=true requires validation_status=live_validated")
        if bool(row.get("ingest_ready")) and not bool(row.get("feed_validated_live")):
            errors.append(f"{prefix} ingest_ready=true requires feed_validated_live=true")
        if bool(row.get("ingest_ready")):
            has_feed = any(str(row.get(field) or "").strip() for field in ("rss_url", "atom_url", "json_feed_url", "sitemap_url"))
            if not has_feed:
                errors.append(f"{prefix} ingest_ready=true requires a validated feed URL")

    missing_states = sorted(US_STATES_PLUS_DC - states_seen)
    if missing_states:
        errors.append(f"national registry missing states: {', '.join(missing_states)}")
    for state in sorted(US_STATES_PLUS_DC & states_seen):
        counts = by_state_type.get(state, {})
        if counts.get("public_media", 0) < 1:
            errors.append(f"state {state} missing required public_media source")
        if counts.get("nonprofit_newsroom", 0) < 1:
            errors.append(f"state {state} missing required nonprofit_newsroom source")
        if counts.get("local_news", 0) < 1:
            errors.append(f"state {state} missing required local_news source")
    return errors


def _candidate_feed_urls(homepage_url: str) -> list[tuple[str, str]]:
    if not _is_valid_url(homepage_url):
        return []
    base = homepage_url.rstrip("/")
    return [
        (urljoin(base + "/", "feed"), "rss"),
        (urljoin(base + "/", "rss"), "rss"),
        (urljoin(base + "/", "feeds"), "rss"),
        (urljoin(base + "/", "rss.xml"), "rss"),
        (urljoin(base + "/", "atom.xml"), "atom"),
        (urljoin(base + "/", "index.xml"), "rss"),
        (urljoin(base + "/", "feed.json"), "json_feed"),
        (urljoin(base + "/", ".well-known/feed.json"), "json_feed"),
        (urljoin(base + "/", "sitemap.xml"), "sitemap"),
    ]


def discover_feed_metadata(
    sources: list[dict[str, Any]],
    *,
    timeout_seconds: int = 4,
    max_fetch_checks: int = 40,
) -> list[dict[str, Any]]:
    checked_cache: dict[str, tuple[bool, int | None, str | None]] = {}
    fetch_checks_used = 0
    enriched: list[dict[str, Any]] = []
    for source in sources:
        row = dict(source)
        found_type = ""
        found_url = ""
        for field, feed_type in (
            ("rss_url", "rss"),
            ("atom_url", "atom"),
            ("json_feed_url", "json_feed"),
            ("sitemap_url", "sitemap"),
        ):
            url = str(row.get(field) or "").strip()
            if not url:
                continue
            if url not in checked_cache:
                if fetch_checks_used >= max_fetch_checks:
                    checked_cache[url] = (False, None, "fetch_check_budget_exhausted")
                else:
                    checked_cache[url] = _fetch_status(url, timeout_seconds=timeout_seconds)
                    fetch_checks_used += 1
            ok, _, _ = checked_cache[url]
            if ok:
                found_type = feed_type
                found_url = url
                break
        if not found_type:
            for candidate_url, candidate_type in _candidate_feed_urls(str(row.get("homepage_url") or "")):
                if candidate_url not in checked_cache:
                    if fetch_checks_used >= max_fetch_checks:
                        checked_cache[candidate_url] = (False, None, "fetch_check_budget_exhausted")
                    else:
                        checked_cache[candidate_url] = _fetch_status(candidate_url, timeout_seconds=timeout_seconds)
                        fetch_checks_used += 1
                ok, _, _ = checked_cache[candidate_url]
                if ok:
                    found_type = candidate_type
                    found_url = candidate_url
                    if candidate_type == "rss":
                        row["rss_url"] = candidate_url
                    elif candidate_type == "atom":
                        row["atom_url"] = candidate_url
                    elif candidate_type == "json_feed":
                        row["json_feed_url"] = candidate_url
                    else:
                        row["sitemap_url"] = candidate_url
                    break
        if found_type in {"rss", "atom", "json_feed"}:
            row["ingest_ready"] = True
            row["collection_method"] = "feed_polling"
            row["feed_discovery_status"] = "feed_validated"
            row["feed_type"] = found_type
            row["feed_health"] = "healthy"
        elif found_type == "sitemap":
            row["ingest_ready"] = False
            row["collection_method"] = "sitemap_polling"
            row["feed_discovery_status"] = "sitemap_only"
            row["feed_type"] = "sitemap"
            row["feed_health"] = "degraded"
        else:
            row["ingest_ready"] = False
            row["collection_method"] = "manual_review"
            row["feed_discovery_status"] = "failed"
            row["feed_type"] = "none"
            row["feed_health"] = "unknown"
        row["last_verified_utc"] = _utc_now()
        if found_url:
            row["notes"] = str(row.get("notes") or "")
        enriched.append(row)
    return enriched


def build_national_coverage_summary(sources: list[dict[str, Any]]) -> dict[str, Any]:
    by_state: dict[str, int] = {}
    by_pillar: dict[str, int] = {}
    by_type: dict[str, int] = {}
    states_with_rural: set[str] = set()
    states_with_urban: set[str] = set()
    states_with_np_or_pm: set[str] = set()
    rss_gaps: list[str] = []
    for row in sources:
        state = str(row.get("state") or "").strip().upper()
        source_type = str(row.get("source_type") or "").strip()
        urban_rural = str(row.get("urban_rural_focus") or "").strip().lower()
        rss_url = str(row.get("rss_url") or "").strip()
        by_state[state] = by_state.get(state, 0) + 1
        by_type[source_type] = by_type.get(source_type, 0) + 1
        for pillar in row.get("pressure_pillars") or []:
            k = str(pillar)
            by_pillar[k] = by_pillar.get(k, 0) + 1
        if urban_rural in {"rural", "mixed"}:
            states_with_rural.add(state)
        if urban_rural in {"urban", "mixed"}:
            states_with_urban.add(state)
        if source_type in {"nonprofit_newsroom", "public_media"}:
            states_with_np_or_pm.add(state)
        if not rss_url:
            rss_gaps.append(str(row.get("source_id") or ""))

    states_covered = sorted(s for s in by_state.keys() if s in US_STATES_PLUS_DC)
    states_missing = sorted(US_STATES_PLUS_DC - set(states_covered))
    weak_states = sorted([s for s in states_covered if by_state.get(s, 0) < 4])
    weak_pillars = sorted([p for p in NATIONAL_ALLOWED_PILLARS if by_pillar.get(p, 0) < 10])
    return {
        "total_sources": len(sources),
        "states_covered": states_covered,
        "states_missing": states_missing,
        "source_counts_by_state": dict(sorted(by_state.items())),
        "source_counts_by_pillar": dict(sorted(by_pillar.items())),
        "rural_focused_source_count": sum(1 for s in sources if str(s.get("urban_rural_focus") or "").lower() == "rural"),
        "urban_focused_source_count": sum(1 for s in sources if str(s.get("urban_rural_focus") or "").lower() == "urban"),
        "source_counts_by_type": dict(sorted(by_type.items())),
        "rss_coverage_gaps": rss_gaps,
        "states_lacking_rural_coverage": sorted(US_STATES_PLUS_DC - states_with_rural),
        "states_lacking_urban_coverage": sorted(US_STATES_PLUS_DC - states_with_urban),
        "states_lacking_nonprofit_public_media_coverage": sorted(US_STATES_PLUS_DC - states_with_np_or_pm),
        "weakly_covered_states": weak_states,
        "weakly_covered_pillars": weak_pillars,
    }


def build_feed_health_summary(sources: list[dict[str, Any]]) -> dict[str, Any]:
    ingest_ready_rows = [row for row in sources if bool(row.get("ingest_ready"))]
    known_feed_url_count = sum(
        1
        for row in sources
        if bool(row.get("feed_url_known"))
        and any(str(row.get(field) or "").strip() for field in ("rss_url", "atom_url", "json_feed_url", "sitemap_url"))
    )
    live_validated_feed_count = sum(1 for row in sources if bool(row.get("feed_validated_live")))
    pending_validation_count = sum(1 for row in sources if str(row.get("validation_status") or "") == "pending_live_validation")
    failed_validation_count = sum(1 for row in sources if str(row.get("validation_status") or "") == "live_failed")
    rss_count = sum(1 for row in sources if str(row.get("rss_url") or "").strip())
    atom_count = sum(1 for row in sources if str(row.get("atom_url") or "").strip())
    json_feed_count = sum(1 for row in sources if str(row.get("json_feed_url") or "").strip())
    sitemap_only_count = sum(
        1
        for row in sources
        if str(row.get("sitemap_url") or "").strip()
        and not any(str(row.get(field) or "").strip() for field in ("rss_url", "atom_url", "json_feed_url"))
    )
    manual_only_count = sum(1 for row in sources if str(row.get("collection_method") or "") == "manual_review")
    failed_discovery_count = sum(1 for row in sources if str(row.get("feed_discovery_status") or "") == "failed")
    broken_feeds = [str(row.get("source_id") or "") for row in sources if str(row.get("feed_health") or "") == "broken"]
    paywalled_sources = [str(row.get("source_id") or "") for row in sources if str(row.get("paywall_status") or "") == "paywalled"]
    robots_disallowed_sources = [str(row.get("source_id") or "") for row in sources if bool(row.get("robots_allowed")) is False]
    by_state: dict[str, int] = {}
    by_pillar: dict[str, int] = {}
    for row in ingest_ready_rows:
        state = str(row.get("state") or "").strip().upper()
        if state:
            by_state[state] = by_state.get(state, 0) + 1
        for pillar in row.get("pressure_pillars") or []:
            token = str(pillar)
            by_pillar[token] = by_pillar.get(token, 0) + 1
    return {
        "total_ingest_ready_sources": len(ingest_ready_rows),
        "known_feed_url_count": known_feed_url_count,
        "live_validated_feed_count": live_validated_feed_count,
        "pending_validation_count": pending_validation_count,
        "failed_validation_count": failed_validation_count,
        "identified_but_failed_or_pending_count": pending_validation_count + failed_validation_count,
        "rss_capable_count": rss_count,
        "atom_count": atom_count,
        "json_feed_count": json_feed_count,
        "sitemap_only_count": sitemap_only_count,
        "manual_only_count": manual_only_count,
        "failed_discovery_count": failed_discovery_count,
        "broken_feeds": broken_feeds,
        "paywalled_sources": paywalled_sources,
        "robots_disallowed_sources": robots_disallowed_sources,
        "ingest_ready_by_state": dict(sorted(by_state.items())),
        "ingest_ready_by_pillar": dict(sorted(by_pillar.items())),
    }


def apply_feed_validation_report(
    sources: list[dict[str, Any]],
    report: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    results = report.get("results")
    if not isinstance(results, list):
        return sources, {"applied_validated": 0, "applied_failed": 0, "unmatched_results": 0}

    live_validated_map: dict[tuple[str, str], dict[str, Any]] = {}
    live_failed_map: dict[tuple[str, str], dict[str, Any]] = {}
    unmatched_results = 0
    for row in results:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("source_id") or "").strip()
        feed_url = str(row.get("feed_url") or "").strip()
        status = str(row.get("validation_status") or row.get("status") or "").strip()
        if not source_id or not feed_url:
            continue
        key = (source_id, feed_url)
        if status == "live_validated":
            live_validated_map[key] = row
        elif status == "live_failed":
            live_failed_map[key] = row
        else:
            unmatched_results += 1

    applied_validated = 0
    applied_failed = 0
    updated: list[dict[str, Any]] = []
    for source in sources:
        row = dict(source)
        source_id = str(row.get("source_id") or "").strip()
        feed_urls = [
            str(row.get("rss_url") or "").strip(),
            str(row.get("atom_url") or "").strip(),
            str(row.get("json_feed_url") or "").strip(),
            str(row.get("sitemap_url") or "").strip(),
        ]
        matched_validated: dict[str, Any] | None = None
        matched_failed: dict[str, Any] | None = None
        for feed_url in feed_urls:
            if not feed_url:
                continue
            key = (source_id, feed_url)
            if key in live_validated_map:
                matched_validated = live_validated_map[key]
                break
            if key in live_failed_map:
                matched_failed = live_failed_map[key]

        if matched_validated:
            row["feed_validated_live"] = True
            row["ingest_ready"] = True
            row["validation_status"] = "live_validated"
            row["feed_health"] = "ok"
            row["last_verified_utc"] = str(matched_validated.get("checked_at_utc") or row.get("last_verified_utc") or "")
            row["feed_url_known"] = bool(row.get("feed_url_known")) or any(feed_urls)
            row["feed_health_detail"] = ""
            row["validation_notes"] = "Validated via source_feed_validation_report."
            applied_validated += 1
        elif matched_failed:
            row["feed_validated_live"] = False
            row["ingest_ready"] = False
            row["validation_status"] = "live_failed"
            row["feed_health"] = "broken"
            row["last_verified_utc"] = str(matched_failed.get("checked_at_utc") or row.get("last_verified_utc") or "")
            row["feed_url_known"] = bool(row.get("feed_url_known")) or any(feed_urls)
            failure_reason = str(matched_failed.get("error") or "").strip()
            row["feed_health_detail"] = failure_reason
            row["validation_notes"] = f"Latest live check failed: {failure_reason}" if failure_reason else "Latest live check failed."
            applied_failed += 1
        updated.append(row)

    return updated, {
        "applied_validated": applied_validated,
        "applied_failed": applied_failed,
        "unmatched_results": unmatched_results,
    }


def write_national_coverage_summary(root: Path, summary: dict[str, Any], path: Path | None = None) -> Path:
    output_path = root / (path or NATIONAL_SUMMARY_PATH)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return output_path


def write_feed_health_summary(root: Path, summary: dict[str, Any], path: Path | None = None) -> Path:
    output_path = root / (path or NATIONAL_FEED_HEALTH_PATH)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return output_path


def _fetch_status(url: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS, user_agent: str = DEFAULT_USER_AGENT) -> tuple[bool, int | None, str | None]:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", 0) or 0)
            return 200 <= status < 400, status, None
    except urllib.error.HTTPError as exc:
        return False, int(exc.code), f"HTTPError: {exc.code}"
    except Exception as exc:
        try:
            fallback = urllib.request.Request(url, method="GET", headers={"User-Agent": user_agent})
            with urllib.request.urlopen(fallback, timeout=timeout_seconds) as response:
                status = int(getattr(response, "status", 0) or 0)
                return 200 <= status < 400, status, None
        except urllib.error.HTTPError as get_exc:
            return False, int(get_exc.code), f"HTTPError: {get_exc.code}"
        except Exception as get_exc:
            return False, None, f"{type(exc).__name__}: {exc}; GET fallback {type(get_exc).__name__}: {get_exc}"


def _recommendation(source_state: str, enabled: bool, fetch_success: bool | None, failure_reason: str | None) -> str:
    if source_state == "manual_only":
        return "Add curated records to manual_sources.json for this source each week."
    if source_state == "diagnostics_only":
        return "Track for diagnostics only; do not use as public source input until upgraded."
    if source_state == "disabled":
        return "Disabled source; enable only after stability review."
    if enabled and fetch_success is False:
        return f"Enabled source check failed ({failure_reason or 'unknown'}); verify endpoint stability or mark manual_only."
    return "Enabled for baseline auto collection."


def build_source_health_report(sources: list[dict[str, Any]], *, fetch_check: bool = False, checked_at: str | None = None, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> list[dict[str, Any]]:
    checked = checked_at or _utc_now()
    report: list[dict[str, Any]] = []
    for source in sources:
        enabled = bool(source.get("enabled"))
        source_state = str(source.get("source_state") or ("enabled" if enabled else "disabled"))
        fetch_attempted = bool(fetch_check and enabled and source_state == "enabled")
        fetch_success: bool | None = None
        status_code: int | None = None
        failure_reason: str | None = None
        if fetch_attempted:
            fetch_success, status_code, failure_reason = _fetch_status(str(source.get("url") or ""), timeout_seconds=timeout_seconds)
        row = SourceCheckResult(
            source_id=str(source.get("source_id") or ""),
            url=str(source.get("url") or ""),
            pillar=str(source.get("pillar") or ""),
            geography=str(source.get("geography") or ""),
            source_type=str(source.get("source_type") or ""),
            reliability_tier=str(source.get("reliability_tier") or ""),
            enabled=enabled,
            validation_ok=True,
            fetch_attempted=fetch_attempted,
            fetch_success=fetch_success,
            status_code=status_code,
            failure_reason=failure_reason,
            checked_at=checked,
            source_state=source_state,
            recommendation=_recommendation(source_state, enabled, fetch_success, failure_reason),
        )
        report.append(row.__dict__)
    return report


def summarize_source_health(report: list[dict[str, Any]]) -> dict[str, Any]:
    out = {
        "sources_configured": len(report),
        "enabled_sources": 0,
        "manual_only_sources": 0,
        "diagnostics_only_sources": 0,
        "disabled_sources": 0,
        "checked_failures": 0,
    }
    for row in report:
        state = str(row.get("source_state") or "")
        if state == "enabled":
            out["enabled_sources"] += 1
        elif state == "manual_only":
            out["manual_only_sources"] += 1
        elif state == "diagnostics_only":
            out["diagnostics_only_sources"] += 1
        elif state == "disabled":
            out["disabled_sources"] += 1
        if row.get("fetch_attempted") and row.get("fetch_success") is False:
            out["checked_failures"] += 1
    return out


def write_source_health_report(root: Path, report: list[dict[str, Any]], as_of_date: str) -> Path:
    path = root / "output" / "dispatches" / "american-pressure" / "source_health" / f"{as_of_date}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path
