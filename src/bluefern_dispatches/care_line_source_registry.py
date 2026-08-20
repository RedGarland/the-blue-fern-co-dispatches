from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bluefern_dispatches.care_line_record import JURISDICTIONS_BY_CODE


SOURCE_REGISTRY_SCHEMA_VERSION = "bluefern.care_line.source_registry.v1"

SUPPORTED_ADAPTERS = {"rss", "atom", "json_feed", "sitemap", "structured_index"}
SOURCE_TYPES = {
    "government_regulator",
    "government_health_department",
    "federal_source",
    "healthcare_organization",
    "trade_publication",
    "local_publisher",
    "regional_publisher",
    "public_radio",
}
GEOGRAPHIC_SCOPES = {"national", "state", "regional", "local"}
AUTHORITY_LEVELS = {"primary", "regulator", "sector", "secondary"}

_CARE_LINE_SOURCE_URL_OVERRIDES: dict[str, dict[str, str]] = {
    "hhs-news": {
        "feed_url": "https://www.hhs.gov/press-room/index.html?page=0",
        "homepage_url": "https://www.hhs.gov/press-room/index.html?page=0",
    },
    "hrsa-news": {
        "feed_url": "https://www.hrsa.gov/about/news/press-releases?page=1",
        "homepage_url": "https://www.hrsa.gov/about/news/press-releases?page=1",
    },
    "calmatters-health": {
        "feed_url": "https://calmatters.org/category/health/",
        "homepage_url": "https://calmatters.org/category/health/",
    },
    "ct-mirror-health": {
        "feed_url": "https://ctmirror.org/health/",
        "homepage_url": "https://ctmirror.org/health/",
    },
    "ohio-capital-journal-health": {
        "feed_url": "https://ohiocapitaljournal.com/category/health-care/",
        "homepage_url": "https://ohiocapitaljournal.com/category/health-care/",
    },
    "missouri-independent-health": {
        "feed_url": "https://missouriindependent.com/category/health-care/",
        "homepage_url": "https://missouriindependent.com/category/health-care/",
    },
    "michigan-advance-health": {
        "feed_url": "https://michiganadvance.com/category/health-care/",
        "homepage_url": "https://michiganadvance.com/category/health-care/",
    },
    "kaiser-permanente-news": {
        "feed_url": "https://about.kaiserpermanente.org/rss-feeds/main-rss",
        "homepage_url": "https://about.kaiserpermanente.org/news",
    },
    "mayo-clinic-news": {
        "feed_url": "https://newsnetwork.mayoclinic.org/category/news-cycle/?pg=1",
        "homepage_url": "https://newsnetwork.mayoclinic.org/category/news-cycle/?pg=1",
    },
    "cleveland-clinic-newsroom": {
        "feed_url": "https://newsroom.clevelandclinic.org/news-releases",
        "homepage_url": "https://newsroom.clevelandclinic.org/news-releases",
    },
    "aha-news": {
        "feed_url": "https://www.aha.org/news",
        "homepage_url": "https://www.aha.org/news",
    },
    "gu-dphss": {
        "feed_url": "https://dphss.guam.gov/",
        "homepage_url": "https://dphss.guam.gov/",
    },
    "rural-health-info-hub": {
        "feed_url": "https://www.ruralhealthinfo.org/rss/news.xml",
        "homepage_url": "https://www.ruralhealthinfo.org/news",
    },
}
SOURCE_CATEGORIES = {
    "federal_health_authority",
    "federal_emergency_health",
    "federal_bankruptcy_notice",
    "national_hospital_association",
    "national_rural_health",
    "national_pharmacy_access",
    "tribal_health_authority",
    "territorial_health_authority",
    "health_department",
    "facility_licensing",
    "certificate_of_need",
    "hospital_association",
    "rural_health",
    "public_meeting_regulatory",
    "health_system_newsroom",
    "bankruptcy_or_closure_notice",
    "local_health_reporting",
    "medicaid_agency",
    "behavioral_health_authority",
    "pharmacy_board",
    "ems_regulator",
    "veterans_health",
}
COLLECTION_METHODS = {
    "feed_polling",
    "sitemap_polling",
    "structured_index_polling",
    "manual_review",
    "search_only",
}
SEARCHABILITIES = {"feed", "site_search", "manual_only", "mixed"}
ACTIVE_STATUSES = {"active", "inactive", "unknown"}
READINESS_STATUSES = {"AUTOMATED_READY", "AUTOMATED_PARTIAL", "MANUAL_REVIEW_ONLY", "BLOCKED", "DISABLED"}

OFFICIAL_AUTHORITY_CATEGORIES = {
    "federal_health_authority",
    "federal_emergency_health",
    "territorial_health_authority",
    "health_department",
    "facility_licensing",
    "certificate_of_need",
    "medicaid_agency",
    "behavioral_health_authority",
    "pharmacy_board",
    "ems_regulator",
    "tribal_health_authority",
}
OPERATIONAL_NOTICE_CATEGORIES = {
    "health_system_newsroom",
    "bankruptcy_or_closure_notice",
    "hospital_association",
    "facility_licensing",
    "certificate_of_need",
}
RURAL_HEALTH_CATEGORIES = {"national_rural_health", "rural_health", "tribal_health_authority"}
REGULATORY_CATEGORIES = {
    "public_meeting_regulatory",
    "certificate_of_need",
    "facility_licensing",
    "medicaid_agency",
    "behavioral_health_authority",
    "pharmacy_board",
    "ems_regulator",
}
LOCAL_REPORTING_CATEGORIES = {"local_health_reporting"}
SPECIALTY_CATEGORIES = {
    "tribal_health_authority",
    "behavioral_health_authority",
    "pharmacy_board",
    "ems_regulator",
    "national_pharmacy_access",
    "veterans_health",
}
NATIONAL_SUPPORT_CATEGORIES = {
    "federal_health_authority",
    "federal_emergency_health",
    "federal_bankruptcy_notice",
    "national_hospital_association",
    "national_rural_health",
    "national_pharmacy_access",
    "tribal_health_authority",
}

SOURCE_COVERAGE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "health_department": ("REQUIRED_CORE",) * 56,
    "facility_licensing": ("REQUIRED_CORE",) * 56,
    "certificate_of_need": ("HIGH_VALUE",) * 56,
    "hospital_association": ("HIGH_VALUE",) * 56,
    "rural_health": ("HIGH_VALUE",) * 56,
    "public_meeting_regulatory": ("HIGH_VALUE",) * 56,
    "health_system_newsroom": ("HIGH_VALUE",) * 56,
    "bankruptcy_or_closure_notice": ("HIGH_VALUE",) * 56,
    "local_health_reporting": ("REQUIRED_CORE",) * 56,
    "medicaid_agency": ("REQUIRED_CORE",) * 56,
    "behavioral_health_authority": ("HIGH_VALUE",) * 56,
    "pharmacy_board": ("HIGH_VALUE",) * 56,
    "ems_regulator": ("HIGH_VALUE",) * 56,
    "tribal_health_authority": ("OPTIONAL",) * 56,
    "territorial_health_authority": ("NOT_APPLICABLE",) * 51 + ("REQUIRED_CORE",) * 5,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_source_id(name: str, feed_url: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    host = (urlparse(feed_url).hostname or "source").removeprefix("www.")
    return f"{stem or 'care-line-source'}-{re.sub(r'[^a-z0-9]+', '-', host.casefold()).strip('-')}"


class CareLineSource(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_id: str
    name: str
    publisher: str
    source_type: str
    feed_url: str
    homepage_url: str
    state: str = ""
    geographic_scope: str
    organization_type: str
    care_line_topics: list[str] = Field(default_factory=list)
    authority_level: str
    expected_update_frequency: str
    enabled: bool = True
    adapter_type: Literal["rss", "atom", "json_feed", "sitemap", "structured_index"]
    requires_html_followup: bool = False
    source_role: str
    historical_depth: str
    notes: str = ""
    created_at: str
    updated_at: str
    allowed_hosts: list[str] = Field(default_factory=list)
    duplicate_feed_reason: str = ""
    source_category: str = "local_health_reporting"
    collection_method: str = "feed_polling"
    searchability: str = "feed"
    what_changes_it_reports: list[str] = Field(default_factory=list)
    limitations: str = ""
    page_active_status: str = "unknown"
    explicit_source_dates: bool = False
    item_permalink_available: bool = True
    archives_distinguishable_from_current: bool = False
    last_verified_date: str = ""
    jurisdiction_scope: str = ""

    @model_validator(mode="after")
    def validate_source(self) -> "CareLineSource":
        if not self.source_id.strip():
            raise ValueError("source_id is required")
        if not self.publisher.strip():
            raise ValueError("publisher is required")
        if self.source_type not in SOURCE_TYPES:
            raise ValueError(f"unsupported source_type: {self.source_type}")
        if self.adapter_type not in SUPPORTED_ADAPTERS:
            raise ValueError(f"unsupported adapter_type: {self.adapter_type}")
        if self.geographic_scope not in GEOGRAPHIC_SCOPES:
            raise ValueError(f"unsupported geographic_scope: {self.geographic_scope}")
        if self.authority_level not in AUTHORITY_LEVELS:
            raise ValueError(f"unsupported authority_level: {self.authority_level}")
        if self.source_category not in SOURCE_CATEGORIES:
            raise ValueError(f"unsupported source_category: {self.source_category}")
        if self.collection_method not in COLLECTION_METHODS:
            raise ValueError(f"unsupported collection_method: {self.collection_method}")
        if self.searchability not in SEARCHABILITIES:
            raise ValueError(f"unsupported searchability: {self.searchability}")
        if self.page_active_status not in ACTIVE_STATUSES:
            raise ValueError(f"unsupported page_active_status: {self.page_active_status}")
        for field_name, value in (("feed_url", self.feed_url), ("homepage_url", self.homepage_url)):
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")
        if self.geographic_scope == "state" and not self.state:
            raise ValueError("state is required for state-scope sources")
        if self.state and self.state not in JURISDICTIONS_BY_CODE:
            raise ValueError(f"unsupported jurisdiction code: {self.state}")
        if self.enabled and not collection_method_is_supported(self.collection_method, self.adapter_type):
            raise ValueError(
                f"enabled source requires a supported collection path: {self.source_id} "
                f"({self.collection_method} / {self.adapter_type})"
            )
        return self


class CareLineSourceRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SOURCE_REGISTRY_SCHEMA_VERSION
    sources: list[CareLineSource]

    @model_validator(mode="after")
    def validate_registry(self) -> "CareLineSourceRegistry":
        if self.schema_version != SOURCE_REGISTRY_SCHEMA_VERSION:
            raise ValueError("unsupported source registry schema")
        source_ids = [source.source_id for source in self.sources]
        duplicates = [source_id for source_id, count in Counter(source_ids).items() if count > 1]
        if duplicates:
            raise ValueError("duplicate source_id: " + ", ".join(sorted(duplicates)))
        feed_counts = Counter(source.feed_url for source in self.sources)
        duplicate_feeds = [
            feed_url
            for feed_url, count in feed_counts.items()
            if count > 1 and any(not source.duplicate_feed_reason for source in self.sources if source.feed_url == feed_url)
        ]
        if duplicate_feeds:
            raise ValueError("duplicate feed_url requires duplicate_feed_reason: " + ", ".join(sorted(duplicate_feeds)))
        return self


def load_registry(path: Path, *, include_disabled: bool = False) -> CareLineSourceRegistry:
    payload = json.loads(path.read_text(encoding="utf-8"))
    collection_method_by_adapter = {
        "rss": "feed_polling",
        "atom": "feed_polling",
        "json_feed": "feed_polling",
        "sitemap": "sitemap_polling",
        "structured_index": "structured_index_polling",
    }
    payload["sources"] = [
        {
            **source,
            **_CARE_LINE_SOURCE_URL_OVERRIDES.get(str(source.get("source_id") or ""), {}),
            "collection_method": str(
                source.get("collection_method")
                or collection_method_by_adapter.get(str(source.get("adapter_type") or ""), "")
            ),
        }
        for source in payload.get("sources", [])
    ]
    registry = CareLineSourceRegistry.model_validate(payload)
    normalized_sources = [
        source.model_copy(update=_CARE_LINE_SOURCE_URL_OVERRIDES.get(source.source_id, {}))
        for source in registry.sources
    ]
    if include_disabled:
        return CareLineSourceRegistry(schema_version=registry.schema_version, sources=normalized_sources)
    return CareLineSourceRegistry(schema_version=registry.schema_version, sources=[source for source in normalized_sources if source.enabled])


def validate_registry_file(path: Path) -> dict[str, Any]:
    registry = load_registry(path, include_disabled=True)
    enabled = [source for source in registry.sources if source.enabled]
    counts = Counter(source.source_type for source in enabled)
    states = sorted({source.state for source in enabled if source.state})
    return {
        "schema_version": SOURCE_REGISTRY_SCHEMA_VERSION,
        "source_count": len(registry.sources),
        "enabled_source_count": len(enabled),
        "disabled_source_count": len(registry.sources) - len(enabled),
        "source_type_counts": dict(sorted(counts.items())),
        "state_count": len(states),
        "states": states,
        "adapter_counts": dict(sorted(Counter(source.adapter_type for source in enabled).items())),
        "category_counts": dict(sorted(Counter(source.source_category for source in enabled).items())),
        "readiness_counts": dict(sorted(Counter(source_readiness_status(source) for source in registry.sources).items())),
    }


def registry_markdown(registry: CareLineSourceRegistry, health_by_source: dict[str, dict[str, Any]] | None = None) -> str:
    health_by_source = health_by_source or {}
    lines = [
        "# Care Line Direct Source Registry Phase 13",
        "",
        f"- Schema: `{registry.schema_version}`",
        f"- Sources: `{len(registry.sources)}`",
        "",
        "| source_id | name | type | state/scope | adapter | enabled | last test | reviewer usable | notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for source in registry.sources:
        health = health_by_source.get(source.source_id, {})
        scope = source.state or source.geographic_scope
        lines.append(
            f"| {source.source_id} | {source.name} | {source.source_type} | {scope} | {source.adapter_type} | {source.enabled} | {health.get('fetch_status', '')}/{health.get('parse_status', '')} | {health.get('reviewer_usable_count', '')} | {source.notes} |"
        )
    return "\n".join(lines) + "\n"


def collection_method_is_supported(collection_method: str, adapter_type: str) -> bool:
    return (
        (collection_method == "feed_polling" and adapter_type in {"rss", "atom", "json_feed"})
        or (collection_method == "sitemap_polling" and adapter_type == "sitemap")
        or (collection_method == "structured_index_polling" and adapter_type == "structured_index")
        or (collection_method == "manual_review")
    )


def source_readiness_status(source: CareLineSource) -> str:
    if not source.enabled:
        return "DISABLED"
    if not collection_method_is_supported(source.collection_method, source.adapter_type):
        return "BLOCKED"
    if source.collection_method == "manual_review":
        return "MANUAL_REVIEW_ONLY"
    if source.requires_html_followup:
        return "AUTOMATED_PARTIAL"
    return "AUTOMATED_READY"


def source_readiness_reason(source: CareLineSource) -> str:
    status = source_readiness_status(source)
    if status == "DISABLED":
        return "disabled_by_registry"
    if status == "BLOCKED":
        return "enabled_without_supported_collection_path"
    if status == "MANUAL_REVIEW_ONLY":
        return "manual_collection_only"
    if status == "AUTOMATED_PARTIAL":
        return "requires_html_followup"
    return ""


def jurisdiction_codes_in_registry(registry: CareLineSourceRegistry) -> list[str]:
    return sorted({source.state for source in registry.sources if source.state})


def coverage_score_components(sources: list[CareLineSource], *, national_source_support: int) -> dict[str, int]:
    return {
        "official_authority_count": sum(1 for source in sources if source.source_category in OFFICIAL_AUTHORITY_CATEGORIES),
        "operational_notice_count": sum(1 for source in sources if source.source_category in OPERATIONAL_NOTICE_CATEGORIES),
        "rural_health_count": sum(1 for source in sources if source.source_category in RURAL_HEALTH_CATEGORIES),
        "regulatory_count": sum(1 for source in sources if source.source_category in REGULATORY_CATEGORIES),
        "local_reporting_count": sum(1 for source in sources if source.source_category in LOCAL_REPORTING_CATEGORIES),
        "specialty_source_count": sum(1 for source in sources if source.source_category in SPECIALTY_CATEGORIES),
        "national_source_support": national_source_support,
    }


def coverage_score(components: dict[str, int]) -> int:
    return (
        components["official_authority_count"] * 4
        + components["operational_notice_count"] * 3
        + components["rural_health_count"] * 2
        + components["regulatory_count"] * 2
        + components["local_reporting_count"] * 2
        + components["specialty_source_count"] * 2
        + min(components["national_source_support"], 5)
    )


def coverage_status(components: dict[str, int], score: int) -> str:
    jurisdiction_specific = (
        components["official_authority_count"]
        + components["operational_notice_count"]
        + components["rural_health_count"]
        + components["regulatory_count"]
        + components["local_reporting_count"]
        + components["specialty_source_count"]
    )
    if score <= 0:
        return "NONE"
    if (
        score >= 15
        and components["official_authority_count"] >= 2
        and components["operational_notice_count"] >= 1
        and components["local_reporting_count"] >= 1
    ):
        return "COMPLETE"
    if (
        score >= 10
        and components["official_authority_count"] >= 1
        and components["local_reporting_count"] >= 1
    ):
        return "STRONG"
    if jurisdiction_specific >= 2 and score >= 5:
        return "PARTIAL"
    return "MINIMAL"
