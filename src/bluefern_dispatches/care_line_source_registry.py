from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        for field_name, value in (("feed_url", self.feed_url), ("homepage_url", self.homepage_url)):
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")
        if self.geographic_scope == "state" and not self.state:
            raise ValueError("state is required for state-scope sources")
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
    registry = CareLineSourceRegistry.model_validate(payload)
    if include_disabled:
        return registry
    return CareLineSourceRegistry(schema_version=registry.schema_version, sources=[source for source in registry.sources if source.enabled])


def validate_registry_file(path: Path) -> dict[str, Any]:
    registry = load_registry(path, include_disabled=True)
    enabled = [source for source in registry.sources if source.enabled]
    counts = Counter(source.source_type for source in enabled)
    states = sorted({source.state for source in enabled if source.state})
    return {
        "schema_version": SOURCE_REGISTRY_SCHEMA_VERSION,
        "source_count": len(registry.sources),
        "enabled_source_count": len(enabled),
        "source_type_counts": dict(sorted(counts.items())),
        "state_count": len(states),
        "states": states,
        "adapter_counts": dict(sorted(Counter(source.adapter_type for source in enabled).items())),
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
